"""Persistent, outbound-only Smara Desktop executor.

The desktop process is deliberately a small capability runner.  It never
opens an inbound listener and it never receives a task unless the hosted API
has leased a step to its paired executor.  Risky capabilities must be paired
explicitly and the task must already have passed Smara's approval gate.
"""
from __future__ import annotations

import argparse
import base64
import contextlib
import difflib
import hashlib
from html.parser import HTMLParser
import json
import logging
import os
import queue
import re
import shlex
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import webbrowser
from dataclasses import dataclass, field
from logging.handlers import RotatingFileHandler
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

import httpx

# The executor runs both as ``smara.desktop_executor`` in tests and as a
# PyInstaller ``__main__`` entry point in the Windows bundle.  Keep the
# package import for normal execution, with a narrow fallback for the bundled
# process where Python does not set ``__package__``.
try:
    from .desktop_integrations import LocalIntegrationCancelled, execute_local_integration
except ImportError:  # pragma: no cover - exercised by the packaged binary
    from desktop_integrations import LocalIntegrationCancelled, execute_local_integration


MAX_FILE_BYTES = 256 * 1024
MAX_DIFF_CHARS = 40_000
MAX_UNDO_ENTRIES = 50
MAX_OUTPUT_CHARS = 32_000
MAX_COMMAND_SECONDS = 60
MAX_BROWSER_INSPECT_BYTES = 1_000_000
MAX_BROWSER_TEXT_CHARS = 16_000
MAX_BROWSER_DOM_ELEMENTS = 100
MAX_BROWSER_DOM_SCAN_ELEMENTS = 1_000
MAX_BROWSER_ELEMENT_TEXT_CHARS = 500
MAX_BROWSER_ATTR_CHARS = 500
MAX_BROWSER_DOWNLOAD_BYTES = 50 * 1024 * 1024
MAX_WORKSPACE_TREE_ENTRIES = 500
MAX_WORKSPACE_TREE_DEPTH = 6
MAX_WORKSPACE_SEARCH_FILES = 100
MAX_WORKSPACE_SEARCH_MATCHES = 200
MAX_WORKSPACE_QUERY_CHARS = 240
MAX_WORKSPACE_FILENAME_MATCHES = 200
MAX_CHANGED_FILES = 100
MAX_ARTIFACT_FILES = 20
MAX_ARTIFACT_BYTES = 8 * 1024 * 1024
MAX_GIT_LINES = 100
MAX_GIT_COMMITS = 20
DEFAULT_CAPABILITIES = ["local_file_read"]
STATE_ENV = "SMARA_DESKTOP_STATE"
CREDENTIALS_ENV = "SMARA_DESKTOP_CREDENTIALS"
UNDO_DIR_NAME = "undo"
LOG = logging.getLogger("smara.desktop")
_CREDENTIAL_NAME = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")

# Recipes are deterministic convenience names, not a second command
# language. They do not accept extra flags, so a hosted task cannot turn a
# recipe into arbitrary code by smuggling arguments through the payload.
LOCAL_RECIPES: dict[str, tuple[str, ...]] = {
    "python.test": ("python", "-m", "pytest", "-q"),
    "python.compile": ("python", "-m", "compileall", "-q", "."),
    "node.test": ("npm", "test"),
    "node.build": ("npm", "run", "build"),
    "rust.test": ("cargo", "test"),
    "rust.check": ("cargo", "check"),
    "git.diff-check": ("git", "diff", "--check"),
}


class ExecutionCancelled(RuntimeError):
    """A local action stopped after its hosted task was cancelled."""


class _PageTextExtractor(HTMLParser):
    """Small dependency-free text extractor for the local inspection mode."""
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self._in_title = False
        self._ignored_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:  # type: ignore[override]
        lowered = tag.lower()
        if lowered == "title":
            self._in_title = True
        if lowered in {"script", "style", "noscript", "svg"}:
            self._ignored_depth += 1

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered == "title":
            self._in_title = False
        if lowered in {"script", "style", "noscript", "svg"} and self._ignored_depth:
            self._ignored_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        normalized = " ".join(data.split())
        if not normalized:
            return
        if self._in_title:
            self.title = f"{self.title} {normalized}".strip()[:500]
        self.parts.append(normalized)


class _PageDomExtractor(HTMLParser):
    """Extract a small, non-executable DOM summary for local inspection.

    This intentionally is not a browser automation surface: scripts, styles,
    SVGs, event handlers, and every unbounded attribute are discarded.  The
    result is useful for locating headings, links, forms, and controls while
    keeping browser sessions and cookies out of the hosted task entirely.
    """

    _SEMANTIC_TAGS = {
        "a", "article", "button", "footer", "form", "h1", "h2", "h3", "h4",
        "header", "img", "input", "main", "nav", "option", "section", "select",
        "textarea",
    }
    _IGNORED_TAGS = {"script", "style", "noscript", "svg", "template"}
    _ATTRIBUTES = {
        "id", "class", "role", "aria-label", "title", "href", "src", "alt",
        "name", "type", "value", "placeholder",
    }

    def __init__(self, *, semantic_only: bool, base_url: str, max_scan: int = MAX_BROWSER_DOM_SCAN_ELEMENTS) -> None:
        super().__init__(convert_charrefs=True)
        self.semantic_only = semantic_only
        self.base_url = base_url
        self.max_scan = max_scan
        self.title = ""
        self._in_title = False
        self.elements: list[dict[str, object]] = []
        self._stack: list[tuple[str, dict[str, object] | None, bool]] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:  # type: ignore[override]
        lowered = tag.lower()
        if lowered == "title":
            self._in_title = True
        if lowered in self._IGNORED_TAGS:
            self._ignored_depth += 1
            self._stack.append((lowered, None, True))
            return
        if self._ignored_depth:
            self._stack.append((lowered, None, False))
            return
        if len(self.elements) >= self.max_scan:
            self._stack.append((lowered, None, False))
            return
        if self.semantic_only and lowered not in self._SEMANTIC_TAGS:
            self._stack.append((lowered, None, False))
            return
        filtered: dict[str, str] = {}
        for name, value in attrs:
            key = str(name).lower()
            if key not in self._ATTRIBUTES or value is None:
                continue
            rendered = str(value)[:MAX_BROWSER_ATTR_CHARS]
            if key in {"href", "src"}:
                rendered = urljoin(self.base_url, rendered)[:MAX_BROWSER_ATTR_CHARS]
            filtered[key] = rendered
        element: dict[str, object] = {"tag": lowered, "attributes": filtered, "text": ""}
        self.elements.append(element)
        self._stack.append((lowered, element, False))

    def handle_startendtag(self, tag: str, attrs) -> None:  # type: ignore[override]
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered == "title":
            self._in_title = False
        match_index = None
        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index][0] == lowered:
                match_index = index
                break
        if match_index is None:
            return
        removed = self._stack[match_index:]
        del self._stack[match_index:]
        self._ignored_depth = max(0, self._ignored_depth - sum(1 for _, _, ignored in removed if ignored))

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        normalized = " ".join(data.split())
        if not normalized:
            return
        if self._in_title:
            self.title = f"{self.title} {normalized}".strip()[:MAX_BROWSER_ATTR_CHARS]
        # Include descendant text in every selected ancestor.  This keeps a
        # button or article useful even when it contains nested spans/labels,
        # while each child still exposes its own more precise text.
        for _, element, _ in self._stack:
            if element is None:
                continue
            previous = str(element.get("text") or "")
            joined = f"{previous} {normalized}".strip()
            element["text"] = joined[:MAX_BROWSER_ELEMENT_TEXT_CHARS]


def default_state_path() -> Path:
    configured = os.getenv(STATE_ENV)
    if configured:
        return Path(configured)
    root = Path(os.getenv("APPDATA", Path.home() / ".config")) / "Smara"
    return root / "desktop.json"


def default_log_path() -> Path:
    root = Path(os.getenv("LOCALAPPDATA", Path.home() / ".local")) / "Smara" / "logs"
    return root / "desktop.log"


def default_credentials_path() -> Path:
    configured = os.getenv(CREDENTIALS_ENV)
    if configured:
        return Path(configured)
    root = Path(os.getenv("APPDATA", Path.home() / ".config")) / "Smara"
    return root / "credentials.json"


def _protect_windows(value: str) -> str:
    """Protect an executor bearer with the current Windows user's DPAPI key."""
    if os.name != "nt":
        return value
    import ctypes
    from ctypes import wintypes

    class DataBlob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

    raw = value.encode("utf-8")
    buffer = ctypes.create_string_buffer(raw)
    source = DataBlob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    protected = DataBlob()
    if not ctypes.windll.crypt32.CryptProtectData(ctypes.byref(source), None, None, None, None, 0x1, ctypes.byref(protected)):
        raise ctypes.WinError()
    try:
        return base64.b64encode(ctypes.string_at(protected.pbData, protected.cbData)).decode("ascii")
    finally:
        ctypes.windll.kernel32.LocalFree(protected.pbData)


def _unprotect_windows(value: str) -> str:
    if os.name != "nt":
        return value
    import ctypes
    from ctypes import wintypes

    class DataBlob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

    raw = base64.b64decode(value.encode("ascii"), validate=True)
    buffer = ctypes.create_string_buffer(raw)
    source = DataBlob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    clear = DataBlob()
    if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 0x1, ctypes.byref(clear)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(clear.pbData, clear.cbData).decode("utf-8")
    finally:
        ctypes.windll.kernel32.LocalFree(clear.pbData)


def _credential_records(path: Path | None = None) -> dict[str, dict]:
    path = path or default_credentials_path()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise RuntimeError("The local credential vault could not be read.") from exc
    if not isinstance(value, dict):
        raise RuntimeError("The local credential vault is invalid.")
    return {key: item for key, item in value.items() if isinstance(key, str) and isinstance(item, dict)}


def _write_credential_records(records: dict[str, dict], path: Path | None = None) -> None:
    path = path or default_credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        json.dump(records, handle, ensure_ascii=False, indent=2)
    try:
        os.replace(temporary, path)
        if os.name != "nt":
            path.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)


def save_local_credential(name: str, secret: str, provider: str = "custom", path: Path | None = None) -> None:
    """Store a local tool secret encrypted for the current Windows account."""
    name = name.strip().upper()
    if not _CREDENTIAL_NAME.fullmatch(name):
        raise RuntimeError("Credential name must be an uppercase environment name, for example TAVILY_API_KEY.")
    if not secret or len(secret) > 16_384:
        raise RuntimeError("Credential value must be between 1 and 16384 characters.")
    records = _credential_records(path)
    records[name] = {
        "provider": (provider.strip().lower() or "custom")[:40],
        "protected": _protect_windows(secret),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_credential_records(records, path)


def delete_local_credential(name: str, path: Path | None = None) -> bool:
    records = _credential_records(path)
    removed = records.pop(name.strip().upper(), None) is not None
    _write_credential_records(records, path)
    return removed


def local_credential_summaries(path: Path | None = None) -> list[dict[str, str]]:
    return [
        {"name": name, "provider": str(item.get("provider") or "custom"), "updated_at": str(item.get("updated_at") or "")}
        for name, item in sorted(_credential_records(path).items())
    ]


def resolve_local_credential(name: str, path: Path | None = None) -> str:
    """Resolve one protected value for the trusted desktop parent process.

    This command is intentionally separate from ``--credential-list``: the
    normal executor never prints secret values, while the native desktop may
    need one value in memory for a direct local-model request. The value is
    written only to the parent's pipe and is never logged or returned to the
    hosted service.
    """
    normalized = name.strip().upper()
    records = _credential_records(path)
    protected = records.get(normalized, {}).get("protected")
    if not _CREDENTIAL_NAME.fullmatch(normalized) or not isinstance(protected, str) or not protected:
        raise RuntimeError(f"Local credential '{normalized}' is not configured on this PC.")
    return _unprotect_windows(protected)


def _resolved_credentials(names: object, path: Path | None = None) -> dict[str, str]:
    if names is None:
        return {}
    if not isinstance(names, list) or len(names) > 12 or not all(isinstance(name, str) for name in names):
        raise RuntimeError("credential_env must be a list of at most 12 local credential names.")
    records = _credential_records(path)
    resolved: dict[str, str] = {}
    for raw_name in names:
        name = raw_name.strip().upper()
        if not _CREDENTIAL_NAME.fullmatch(name) or name not in records:
            raise RuntimeError(f"Local credential '{name}' is not configured on this PC.")
        protected = records[name].get("protected")
        if not isinstance(protected, str) or not protected:
            raise RuntimeError(f"Local credential '{name}' is invalid; save it again.")
        resolved[name] = _unprotect_windows(protected)
    return resolved


def _save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = dict(state)
    token = serialized.pop("token", None)
    if isinstance(token, str) and token:
        if os.name == "nt":
            serialized["token_dpapi"] = _protect_windows(token)
        else:
            serialized["token"] = token
    path.write_text(json.dumps(serialized, ensure_ascii=False), encoding="utf-8")
    if os.name != "nt":
        path.chmod(0o600)


def _load_state(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError) as exc:
        raise RuntimeError(f"Desktop pairing state is unavailable: {path}") from exc
    if isinstance(value, dict) and os.name == "nt" and value.get("token_dpapi"):
        try:
            value["token"] = _unprotect_windows(value["token_dpapi"])
        except (OSError, ValueError) as exc:
            raise RuntimeError("Desktop token cannot be unlocked by this Windows account; pair again.") from exc
    if not isinstance(value, dict) or not value.get("executor_id") or not value.get("token") or not value.get("smara_url"):
        raise RuntimeError("Desktop pairing state is invalid; pair this device again.")
    if os.name == "nt" and "token_dpapi" not in value:
        _save_state(path, value)  # one-time migration away from legacy plaintext state
    return value


def _pause_path(state_path: Path) -> Path:
    return state_path.with_suffix(state_path.suffix + ".paused")


@contextlib.contextmanager
def _single_runner(state_path: Path):
    """Hold a non-blocking file lock so two executor loops cannot run together."""
    lock_path = state_path.with_suffix(state_path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+b")
    if handle.tell() == 0:
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        handle.close()
        raise RuntimeError("Smara Desktop is already running for this state file.") from exc
    try:
        yield
    finally:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def _headers(state: dict) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {state['token']}",
        "X-Smara-Executor-Id": state["executor_id"],
        "Accept": "application/json",
    }


def normalize_pairing_code(code: str) -> str:
    """Return the canonical eight-character code accepted by Smara."""
    normalized = "".join(code.split()).upper()
    if len(normalized) != 8 or any(character not in "0123456789ABCDEF" for character in normalized):
        raise RuntimeError("Pairing code must contain 8 hexadecimal characters.")
    return normalized


def pair(api_url: str, code: str, state_path: Path, *, allowed_roots: list[str] | None = None) -> dict:
    """Consume a one-time pairing code and persist only the scoped device token."""
    response = httpx.post(f"{api_url.rstrip('/')}/v1/executors/pair", json={"code": normalize_pairing_code(code)}, timeout=15)
    if not response.is_success:
        # Preserve the API's actionable detail (invalid/expired/used) instead
        # of collapsing every pairing failure into a generic HTTP 400.
        try:
            detail = response.json().get("detail")
        except (AttributeError, ValueError, TypeError):
            detail = None
        if isinstance(detail, str) and detail.strip():
            raise RuntimeError(detail.strip())
    response.raise_for_status()
    state = {**response.json(), "smara_url": api_url.rstrip("/"), "allowed_roots": allowed_roots or []}
    _save_state(state_path, state)
    return state


def _roots(state: dict) -> list[Path]:
    values = state.get("allowed_roots") or []
    if not isinstance(values, list):
        raise RuntimeError("Desktop approved roots are invalid.")
    roots = [Path(item).expanduser().resolve() for item in values if isinstance(item, str) and item.strip()]
    if not roots:
        raise RuntimeError("No local folders are approved. Pair with --allow-root before using file capabilities.")
    return roots


def _inside_root(path: Path, roots: list[Path]) -> bool:
    return any(path == root or root in path.parents for root in roots)


def _target(raw: object, roots: list[Path], *, must_exist: bool) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise RuntimeError("A local path is required.")
    candidate = Path(raw).expanduser()
    # Resolve the parent even when creating a new file; this rejects traversal
    # and symlink escapes without following an attacker-controlled target.
    try:
        prospective = candidate.resolve(strict=False)
        if not _inside_root(prospective, roots):
            raise RuntimeError("Requested path is outside the desktop owner's approved folders.")
        if must_exist:
            target = candidate.resolve(strict=True)
        else:
            parent = candidate.parent.resolve(strict=True)
            target = parent / candidate.name
    except RuntimeError:
        raise
    except OSError as exc:
        raise RuntimeError("Requested local path does not exist or is inaccessible.") from exc
    if not _inside_root(target, roots):
        raise RuntimeError("Requested path is outside the desktop owner's approved folders.")
    if must_exist and target.is_symlink():
        raise RuntimeError("Symlinked files are not allowed.")
    return target


def _read_file(payload: dict, roots: list[Path]) -> str:
    target = _target(payload.get("path"), roots, must_exist=True)
    if not target.is_file():
        raise RuntimeError("Requested local path is not a regular file.")
    size = target.stat().st_size
    if size > MAX_FILE_BYTES:
        raise RuntimeError(f"Requested local file exceeds the {MAX_FILE_BYTES} byte limit.")
    content = target.read_bytes()
    result: dict[str, object] = {
        "action": "local_file_read",
        "file_name": target.name,
        "bytes_read": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "content_shared": False,
    }
    # Sharing content must be explicit in the task payload.  It is bounded and
    # only reachable after the hosted approval gate has released the step.
    if payload.get("share_content") is True:
        result["content_shared"] = True
        result["content"] = content.decode("utf-8", errors="replace")[:MAX_FILE_BYTES]
    return json.dumps(result, ensure_ascii=False)


def _bounded_int(value: object, *, default: int, minimum: int, maximum: int, field: str) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum or value > maximum:
        raise RuntimeError(f"{field} must be an integer between {minimum} and {maximum}.")
    return value


def _workspace_root(payload: dict, roots: list[Path]) -> Path:
    requested = payload.get("path")
    if requested is None:
        return roots[0]
    target = _target(requested, roots, must_exist=True)
    if not target.is_dir():
        raise RuntimeError("Workspace inspection path must be an approved directory.")
    return target


def _relative_path(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix() or "."


def _list_workspace(payload: dict, roots: list[Path]) -> str:
    root = _workspace_root(payload, roots)
    max_depth = _bounded_int(
        payload.get("max_depth"), default=3, minimum=0, maximum=MAX_WORKSPACE_TREE_DEPTH, field="max_depth"
    )
    max_entries = _bounded_int(
        payload.get("max_entries"), default=200, minimum=1, maximum=MAX_WORKSPACE_TREE_ENTRIES, field="max_entries"
    )
    entries: list[dict[str, object]] = []
    truncated = False
    try:
        for current, directories, files in os.walk(root, topdown=True, followlinks=False):
            current_path = Path(current)
            relative = current_path.relative_to(root)
            depth = len(relative.parts)
            directories[:] = sorted(
                item for item in directories
                if not (current_path / item).is_symlink()
            )
            if depth >= max_depth:
                directories[:] = []
            for directory in directories:
                if len(entries) >= max_entries:
                    truncated = True
                    break
                child = current_path / directory
                entries.append({"path": _relative_path(child, root), "kind": "directory"})
            if truncated:
                break
            for filename in sorted(files):
                if len(entries) >= max_entries:
                    truncated = True
                    break
                child = current_path / filename
                if child.is_symlink() or not child.is_file():
                    continue
                try:
                    size = child.stat().st_size
                except OSError:
                    continue
                entries.append({"path": _relative_path(child, root), "kind": "file", "bytes": size})
            if truncated:
                break
    except OSError as exc:
        raise RuntimeError("Could not inspect the approved workspace.") from exc
    return json.dumps({
        "action": "local_workspace_inspect",
        "operation": "list_tree",
        "root": root.name or str(root),
        "entries": entries,
        "truncated": truncated,
    }, ensure_ascii=False)


def _search_workspace(payload: dict, roots: list[Path]) -> str:
    root = _workspace_root(payload, roots)
    query = payload.get("query")
    if not isinstance(query, str) or not query.strip() or len(query.strip()) > MAX_WORKSPACE_QUERY_CHARS:
        raise RuntimeError(f"query must be a non-empty string up to {MAX_WORKSPACE_QUERY_CHARS} characters.")
    needle = query.casefold()
    max_files = _bounded_int(
        payload.get("max_files"), default=50, minimum=1, maximum=MAX_WORKSPACE_SEARCH_FILES, field="max_files"
    )
    max_matches = _bounded_int(
        payload.get("max_matches"), default=50, minimum=1, maximum=MAX_WORKSPACE_SEARCH_MATCHES, field="max_matches"
    )
    scanned_files = 0
    matches: list[dict[str, object]] = []
    truncated = False
    try:
        for current, directories, files in os.walk(root, topdown=True, followlinks=False):
            current_path = Path(current)
            directories[:] = sorted(item for item in directories if not (current_path / item).is_symlink())
            for filename in sorted(files):
                if scanned_files >= max_files or len(matches) >= max_matches:
                    truncated = True
                    break
                child = current_path / filename
                if child.is_symlink() or not child.is_file():
                    continue
                try:
                    if child.stat().st_size > MAX_FILE_BYTES:
                        continue
                    raw = child.read_bytes()
                except OSError:
                    continue
                scanned_files += 1
                if b"\x00" in raw[:8_192]:
                    continue
                text = raw.decode("utf-8", errors="replace")
                for number, line in enumerate(text.splitlines(), 1):
                    if needle not in line.casefold():
                        continue
                    matches.append({
                        "path": _relative_path(child, root),
                        "line": number,
                        "preview": line.strip()[:500],
                    })
                    if len(matches) >= max_matches:
                        truncated = True
                        break
            if truncated:
                break
    except OSError as exc:
        raise RuntimeError("Could not search the approved workspace.") from exc
    return json.dumps({
        "action": "local_workspace_inspect",
        "operation": "search_text",
        "root": root.name or str(root),
        "query": query,
        "scanned_files": scanned_files,
        "matches": matches,
        "truncated": truncated,
    }, ensure_ascii=False)


def _find_workspace_files(payload: dict, roots: list[Path]) -> str:
    root = _workspace_root(payload, roots)
    query = payload.get("query")
    if not isinstance(query, str) or not query.strip() or len(query) > MAX_WORKSPACE_QUERY_CHARS:
        raise RuntimeError(f"Filename query must be between 1 and {MAX_WORKSPACE_QUERY_CHARS} characters.")
    query = query.strip().lower()
    limit = payload.get("max_matches", MAX_WORKSPACE_FILENAME_MATCHES)
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_WORKSPACE_FILENAME_MATCHES:
        raise RuntimeError(f"max_matches must be between 1 and {MAX_WORKSPACE_FILENAME_MATCHES}.")
    matches: list[str] = []
    truncated = False
    try:
        for candidate in root.rglob("*"):
            if candidate.is_symlink():
                continue
            if query not in candidate.name.lower():
                continue
            if len(matches) < limit:
                matches.append(candidate.relative_to(root).as_posix())
            else:
                truncated = True
                break
    except OSError as exc:
        raise RuntimeError("Could not search filenames in the approved workspace.") from exc
    return json.dumps({"action": "local_workspace_inspect", "operation": "find_files", "root": root.name or str(root), "query": query, "matches": matches, "truncated": truncated}, ensure_ascii=False)


def _git_summary(payload: dict, roots: list[Path], state: dict) -> str:
    root = _workspace_root(payload, roots)
    allowlist = state.get("terminal_allowlist") or []
    if "git" not in {Path(item).name.lower() for item in allowlist if isinstance(item, str)}:
        raise RuntimeError("Git inspection requires 'git' in the terminal executable allowlist.")

    def run_git(*arguments: str) -> str:
        try:
            result = subprocess.run(["git", "-C", str(root), *arguments], capture_output=True, text=True, timeout=10, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError("Git inspection could not start git.") from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "not a Git repository").strip()
            raise RuntimeError(f"Git inspection failed: {detail[:300]}")
        return result.stdout

    branch_lines = [line for line in run_git("status", "--short", "--branch").splitlines() if line.strip()]
    stat_lines = [line for line in run_git("diff", "--stat").splitlines() if line.strip()]
    commits = [line for line in run_git("log", f"-n{MAX_GIT_COMMITS}", "--pretty=format:%h%x09%s").splitlines() if line.strip()]
    return json.dumps({
        "action": "local_workspace_inspect", "operation": "git_summary", "root": root.name or str(root),
        "branch": branch_lines[0].removeprefix("## ") if branch_lines and branch_lines[0].startswith("## ") else None,
        "status": branch_lines[:MAX_GIT_LINES], "diff_stat": stat_lines[:MAX_GIT_LINES], "recent_commits": commits[:MAX_GIT_COMMITS],
        "truncated": len(branch_lines) > MAX_GIT_LINES or len(stat_lines) > MAX_GIT_LINES,
    }, ensure_ascii=False)


def _workspace_inspect(payload: dict, roots: list[Path]) -> str:
    operation = payload.get("operation", "read_file")
    if operation == "read_file":
        return _read_file(payload, roots)
    if operation == "list_tree":
        return _list_workspace(payload, roots)
    if operation == "search_text":
        return _search_workspace(payload, roots)
    if operation == "find_files":
        return _find_workspace_files(payload, roots)
    if operation == "git_summary":
        return _git_summary(payload, roots, payload.get("_state", {}))
    raise RuntimeError("local_file_read supports read_file, list_tree, search_text, find_files, and git_summary operations only.")


def _atomic_bytes(path: Path, data: bytes) -> None:
    """Replace one validated file without ever exposing a partial write."""
    with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(data)
        handle.flush()
        with contextlib.suppress(OSError):
            os.fsync(handle.fileno())
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _existing_file_bytes(path: Path, *, label: str = "local file") -> bytes:
    if not path.exists():
        return b""
    if path.is_symlink():
        raise RuntimeError("Symlinked files are not allowed.")
    if not path.is_file():
        raise RuntimeError(f"{label.capitalize()} must be a regular file.")
    try:
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            raise RuntimeError(f"{label.capitalize()} exceeds the {MAX_FILE_BYTES} byte limit.")
        return path.read_bytes()
    except OSError as exc:
        raise RuntimeError(f"Could not read the existing {label}.") from exc


def _text_diff(name: str, before: bytes, after: bytes) -> tuple[str, bool]:
    """Create a bounded, human-readable diff without leaking absolute paths."""
    try:
        before_text = before.decode("utf-8")
        after_text = after.decode("utf-8")
    except UnicodeDecodeError:
        message = f"Binary or non-UTF-8 content: {len(before)} bytes -> {len(after)} bytes"
        return message, False
    diff = "".join(difflib.unified_diff(
        before_text.splitlines(keepends=True),
        after_text.splitlines(keepends=True),
        fromfile=f"{name} (before)",
        tofile=f"{name} (after)",
        lineterm="",
    ))
    truncated = len(diff) > MAX_DIFF_CHARS
    return diff[:MAX_DIFF_CHARS], truncated


def _metadata_diff(operation: str, source: Path, destination: Path) -> str:
    return (
        f"--- {source.name} (before)\n"
        f"+++ {destination.name} (after)\n"
        f"{operation}: {source.name} -> {destination.name}"
    )


def _undo_dir(state: dict | None, roots: list[Path]) -> Path:
    """Keep undo snapshots in Smara's local app data, never in hosted state."""
    configured = (state or {}).get("_state_path")
    if isinstance(configured, str) and configured.strip():
        directory = Path(configured).expanduser().resolve().parent / UNDO_DIR_NAME
    else:
        # Direct unit-test calls have no state file. Keep this fallback inside
        # the approved root so it remains bounded by the caller's sandbox.
        directory = roots[0] / ".smara-undo"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _undo_ledger_path(directory: Path) -> Path:
    return directory / "ledger.json"


def _load_undo_entries(directory: Path) -> list[dict]:
    try:
        value = json.loads(_undo_ledger_path(directory).read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError):
        return []
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)][:MAX_UNDO_ENTRIES]


def _save_undo_entries(directory: Path, entries: list[dict]) -> None:
    ledger = _undo_ledger_path(directory)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=directory, delete=False) as handle:
        temporary = Path(handle.name)
        json.dump(entries[:MAX_UNDO_ENTRIES], handle, ensure_ascii=False, indent=2)
        handle.flush()
        with contextlib.suppress(OSError):
            os.fsync(handle.fileno())
    try:
        os.replace(temporary, ledger)
    finally:
        temporary.unlink(missing_ok=True)


def _remember_undo(directory: Path, record: dict, snapshot: bytes | None) -> str:
    undo_id = f"undo_{uuid.uuid4().hex}"
    if snapshot is not None:
        snapshot_path = directory / f"{undo_id}.bin"
        _atomic_bytes(snapshot_path, snapshot)
        record["snapshot"] = snapshot_path.name
    record["undo_id"] = undo_id
    record["created_at"] = datetime.now(timezone.utc).isoformat()
    entries = _load_undo_entries(directory)
    entries.insert(0, record)
    stale = entries[MAX_UNDO_ENTRIES:]
    _save_undo_entries(directory, entries)
    for item in stale:
        snapshot_name = item.get("snapshot")
        if isinstance(snapshot_name, str) and re.fullmatch(r"undo_[0-9a-f]{32}\.bin", snapshot_name):
            (directory / snapshot_name).unlink(missing_ok=True)
    return undo_id


def _preview_payload(operation: str, target: Path, before: bytes, after: bytes, *, changed: bool, diff: str, truncated: bool) -> dict:
    return {
        "operation": operation,
        "file_name": target.name,
        "changed": changed,
        "bytes_before": len(before),
        "bytes_after": len(after),
        "diff": diff,
        "diff_truncated": truncated,
    }


def _write_file(payload: dict, roots: list[Path], state: dict | None = None) -> str:
    """Preview and safely apply bounded workspace edits.

    ``preview_only`` is a read-only planning operation. Mutations always
    compute and return the same preview before the atomic write/rename/delete,
    and successful mutations receive a local-only ``undo_id``.
    """
    operation = payload.get("operation")
    if operation is None:
        operation = "append" if payload.get("append") is True else "write"
    if operation == "replace":
        operation = "write"
    if not isinstance(operation, str) or operation not in {"write", "append", "patch", "rename", "move", "delete", "undo"}:
        raise RuntimeError("local_file_write supports write, append, patch, rename, move, delete, and undo operations.")

    directory = _undo_dir(state, roots)
    if operation == "undo":
        return _undo_file(payload, roots, directory)

    if operation in {"rename", "move"}:
        source = _target(payload.get("path"), roots, must_exist=True)
        destination = _target(payload.get("new_path"), roots, must_exist=False)
        if source == destination:
            raise RuntimeError("Source and destination paths must be different.")
        if destination.exists() or destination.is_symlink():
            raise RuntimeError("The destination already exists; refusing to overwrite it.")
        before = _existing_file_bytes(source, label="source file")
        diff = _metadata_diff(operation, source, destination)
        preview = {"operation": operation, "from": source.name, "to": destination.name, "changed": True, "diff": diff, "diff_truncated": False}
        if payload.get("preview_only") is True:
            return json.dumps({"action": "local_file_preview", "preview_only": True, "preview": preview}, ensure_ascii=False)
        undo_id = _remember_undo(directory, {"kind": "rename", "source": str(source), "destination": str(destination), "after_sha256": hashlib.sha256(before).hexdigest()}, None)
        try:
            os.replace(source, destination)
        except OSError:
            # Do not leave an undo record for an operation that never applied.
            _remove_undo_entry(directory, undo_id)
            raise RuntimeError("Could not move the approved file atomically.")
        return json.dumps({"action": "local_file_write", "operation": operation, "file_name": destination.name, "bytes_written": len(before), "sha256": hashlib.sha256(before).hexdigest(), "preview": preview, "undo_id": undo_id, "undo_available": True}, ensure_ascii=False)

    target = _target(payload.get("path"), roots, must_exist=False)
    before = _existing_file_bytes(target)
    if operation == "delete":
        if not target.exists():
            raise RuntimeError("The file to delete does not exist.")
        after = b""
        diff, truncated = _text_diff(target, before, after)
        preview = _preview_payload(operation, target, before, after, changed=True, diff=diff, truncated=truncated)
        if payload.get("preview_only") is True:
            return json.dumps({"action": "local_file_preview", "preview_only": True, "preview": preview}, ensure_ascii=False)
        undo_id = _remember_undo(directory, {"kind": "delete", "target": str(target), "before_sha256": hashlib.sha256(before).hexdigest()}, before)
        try:
            target.unlink()
        except OSError:
            _remove_undo_entry(directory, undo_id)
            raise RuntimeError("Could not delete the approved file.")
        return json.dumps({"action": "local_file_write", "operation": operation, "file_name": target.name, "bytes_written": 0, "preview": preview, "undo_id": undo_id, "undo_available": True}, ensure_ascii=False)

    if operation in {"write", "append"}:
        content = payload.get("content")
        if not isinstance(content, str):
            raise RuntimeError("local_file_write requires text content.")
        data = content.encode("utf-8")
        if operation == "append":
            after = before + data
            if len(after) > MAX_FILE_BYTES:
                raise RuntimeError("Appended content exceeds the file size limit.")
        else:
            after = data
        changed = before != after
        diff, truncated = _text_diff(target, before, after)
        preview = _preview_payload(operation, target, before, after, changed=changed, diff=diff, truncated=truncated)
        if payload.get("preview_only") is True:
            return json.dumps({"action": "local_file_preview", "preview_only": True, "preview": preview}, ensure_ascii=False)
    else:  # patch
        if not target.exists():
            raise RuntimeError("Patch target does not exist.")
        try:
            source_text = before.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise RuntimeError("Patch target must be UTF-8 text.") from exc
        find = payload.get("find")
        replace = payload.get("replace")
        if not isinstance(find, str) or not find:
            raise RuntimeError("Patch requires a non-empty 'find' string.")
        if not isinstance(replace, str):
            raise RuntimeError("Patch requires a string 'replace' value.")
        if len(find.encode("utf-8")) > MAX_FILE_BYTES or len(replace.encode("utf-8")) > MAX_FILE_BYTES:
            raise RuntimeError(f"Patch content exceeds the {MAX_FILE_BYTES} byte limit.")
        occurrences = source_text.count(find)
        count = payload.get("count")
        if count is None:
            if occurrences != 1:
                raise RuntimeError("Patch is ambiguous; provide count matching every intended occurrence.")
            count = 1
        else:
            count = _bounded_int(count, default=1, minimum=1, maximum=100, field="count")
            if occurrences != count:
                raise RuntimeError(f"Patch count {count} does not match the {occurrences} occurrences found.")
        after = source_text.replace(find, replace, count).encode("utf-8")
        if len(after) > MAX_FILE_BYTES:
            raise RuntimeError(f"Patched content exceeds the {MAX_FILE_BYTES} byte limit.")
        diff, truncated = _text_diff(target, before, after)
        preview = _preview_payload(operation, target, before, after, changed=before != after, diff=diff, truncated=truncated)
        if payload.get("preview_only") is True:
            return json.dumps({"action": "local_file_preview", "preview_only": True, "preview": preview}, ensure_ascii=False)

    if not preview["changed"]:
        return json.dumps({"action": "local_file_write", "operation": operation, "file_name": target.name, "bytes_written": 0, "sha256": hashlib.sha256(after).hexdigest(), "preview": preview, "undo_available": False}, ensure_ascii=False)
    undo_id = _remember_undo(directory, {"kind": "content", "target": str(target), "before_exists": target.exists(), "after_sha256": hashlib.sha256(after).hexdigest()}, before if target.exists() else None)
    try:
        _atomic_bytes(target, after)
    except OSError:
        _remove_undo_entry(directory, undo_id)
        raise RuntimeError("Could not atomically write the approved file.")
    return json.dumps({"action": "local_file_write", "operation": operation, "file_name": target.name, "bytes_written": len(data) if operation in {"write", "append"} else len(after), "sha256": hashlib.sha256(after).hexdigest(), "preview": preview, "undo_id": undo_id, "undo_available": True}, ensure_ascii=False)


def _remove_undo_entry(directory: Path, undo_id: str) -> None:
    entries = _load_undo_entries(directory)
    remaining = [item for item in entries if item.get("undo_id") != undo_id]
    _save_undo_entries(directory, remaining)
    (directory / f"{undo_id}.bin").unlink(missing_ok=True)


def _undo_file(payload: dict, roots: list[Path], directory: Path) -> str:
    undo_id = payload.get("undo_id")
    if not isinstance(undo_id, str) or not re.fullmatch(r"undo_[0-9a-f]{32}", undo_id):
        raise RuntimeError("A valid local undo_id is required.")
    entries = _load_undo_entries(directory)
    record = next((item for item in entries if item.get("undo_id") == undo_id), None)
    if record is None:
        raise RuntimeError("That undo entry is unavailable or has expired.")
    if record.get("undone_at"):
        raise RuntimeError("That local change has already been undone.")
    kind = record.get("kind")
    try:
        if kind == "content":
            target = _target(record.get("target"), roots, must_exist=False)
            current = _existing_file_bytes(target)
            if hashlib.sha256(current).hexdigest() != record.get("after_sha256"):
                raise RuntimeError("The file changed after this edit; refusing to overwrite newer work.")
            if record.get("before_exists"):
                snapshot_name = record.get("snapshot")
                if not isinstance(snapshot_name, str) or not re.fullmatch(r"undo_[0-9a-f]{32}\.bin", snapshot_name):
                    raise RuntimeError("The undo snapshot is invalid.")
                snapshot = (directory / snapshot_name).read_bytes()
                if len(snapshot) > MAX_FILE_BYTES:
                    raise RuntimeError("The undo snapshot exceeds the local file limit.")
                _atomic_bytes(target, snapshot)
            else:
                if target.exists():
                    target.unlink()
        elif kind == "delete":
            target = _target(record.get("target"), roots, must_exist=False)
            if target.exists():
                raise RuntimeError("A file now exists at the deleted path; refusing to overwrite it.")
            snapshot_name = record.get("snapshot")
            if not isinstance(snapshot_name, str) or not re.fullmatch(r"undo_[0-9a-f]{32}\.bin", snapshot_name):
                raise RuntimeError("The undo snapshot is invalid.")
            snapshot = (directory / snapshot_name).read_bytes()
            if len(snapshot) > MAX_FILE_BYTES:
                raise RuntimeError("The undo snapshot exceeds the local file limit.")
            _atomic_bytes(target, snapshot)
        elif kind == "rename":
            source = _target(record.get("source"), roots, must_exist=False)
            destination = _target(record.get("destination"), roots, must_exist=True)
            current = _existing_file_bytes(destination, label="destination file")
            if source.exists() or hashlib.sha256(current).hexdigest() != record.get("after_sha256"):
                raise RuntimeError("The moved file changed; refusing to overwrite newer work.")
            os.replace(destination, source)
        else:
            raise RuntimeError("That undo entry has an unsupported operation.")
    except FileNotFoundError as exc:
        raise RuntimeError("The undo snapshot is unavailable.") from exc
    except OSError as exc:
        raise RuntimeError("Could not restore the local change.") from exc
    record["undone_at"] = datetime.now(timezone.utc).isoformat()
    _save_undo_entries(directory, entries)
    return json.dumps({"action": "local_file_undo", "undo_id": undo_id, "restored": True}, ensure_ascii=False)


def _emit_progress(progress_hook, message: str) -> None:
    """Best-effort status only: never let telemetry change local execution."""
    if progress_hook is None:
        return
    try:
        progress_hook(message[:500])
    except Exception:
        LOG.debug("Could not publish desktop progress", exc_info=True)


def _stop_process(process: subprocess.Popen) -> None:
    """Stop a process tree when a cancellation or time limit arrives."""
    if process.poll() is not None:
        return
    if os.name == "nt":
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, timeout=10, check=False)
    else:
        with contextlib.suppress(OSError):
            process.terminate()
    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(timeout=5)
    if process.poll() is None:
        with contextlib.suppress(OSError):
            process.kill()


def _recipe_argv(payload: dict) -> tuple[list[str], str | None]:
    recipe = payload.get("recipe")
    if recipe is not None:
        if not isinstance(recipe, str) or recipe not in LOCAL_RECIPES:
            available = ", ".join(sorted(LOCAL_RECIPES))
            raise RuntimeError(f"Unknown local recipe. Choose one of: {available}.")
        if payload.get("argv") is not None or payload.get("command") is not None:
            raise RuntimeError("Provide either a named recipe or an argv command, not both.")
        return list(LOCAL_RECIPES[recipe]), recipe
    raw = payload.get("argv")
    if isinstance(raw, list) and all(isinstance(item, str) for item in raw):
        return [item for item in raw if item], None
    if isinstance(payload.get("command"), str):
        command = payload["command"]
        if any(char in command for char in "|&;><`\n\r"):
            raise RuntimeError("Shell operators are not allowed; provide an argv list instead.")
        return shlex.split(command, posix=os.name != "nt"), None
    raise RuntimeError("local_terminal requires a named recipe, argv list, or bounded command.")


def _git_status_files(cwd: Path, state: dict) -> dict[str, str] | None:
    """Return bounded Git paths when Git is explicitly allowed locally."""
    allowlist = state.get("terminal_allowlist") or []
    if "git" not in {Path(item).name.lower() for item in allowlist if isinstance(item, str)}:
        return None
    try:
        result = subprocess.run(
            ["git", "-C", str(cwd), "status", "--short"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    paths: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if len(paths) >= MAX_CHANGED_FILES:
            break
        line = line.strip()
        if len(line) < 4:
            continue
        status = line[:2]
        path = line[3:].strip()
        # Git represents a rename as "old -> new"; the new path is what a
        # user needs to inspect after a recipe completes.
        if " -> " in path:
            path = path.rsplit(" -> ", 1)[-1]
        if path:
            paths[path] = status
    return paths


def _collect_artifacts(payload: dict, cwd: Path, roots: list[Path]) -> list[dict[str, object]]:
    raw = payload.get("artifact_paths")
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > MAX_ARTIFACT_FILES or not all(isinstance(item, str) for item in raw):
        raise RuntimeError(f"artifact_paths must contain at most {MAX_ARTIFACT_FILES} local paths.")
    artifacts: list[dict[str, object]] = []
    for value in raw:
        artifact = _target(value, roots, must_exist=True)
        if cwd != artifact and cwd not in artifact.parents:
            raise RuntimeError("Artifact paths must stay inside the recipe working directory.")
        if not artifact.is_file() or artifact.is_symlink():
            raise RuntimeError("Each artifact path must be a regular local file.")
        size = artifact.stat().st_size
        if size > MAX_ARTIFACT_BYTES:
            raise RuntimeError(f"Artifact exceeds the {MAX_ARTIFACT_BYTES} byte limit.")
        artifacts.append({
            "path": artifact.relative_to(cwd).as_posix(),
            "bytes": size,
            "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
        })
    return artifacts


def _terminal(payload: dict, roots: list[Path], state: dict, *, checkpoint=None, progress_hook=None) -> str:
    argv, recipe = _recipe_argv(payload)
    if not argv:
        raise RuntimeError("local_terminal received an empty command.")
    allowlist = state.get("terminal_allowlist") or []
    if not isinstance(allowlist, list) or not allowlist:
        raise RuntimeError("Terminal capability is disabled until an executable allowlist is configured.")
    executable = Path(argv[0]).name.lower()
    if executable not in {str(item).lower() for item in allowlist if isinstance(item, str)}:
        raise RuntimeError(f"Executable '{executable}' is not in the desktop allowlist.")
    cwd_value = payload.get("cwd") or str(roots[0])
    cwd = _target(cwd_value, roots, must_exist=True)
    if not cwd.is_dir():
        raise RuntimeError("Terminal working directory is not an approved folder.")
    before_files = _git_status_files(cwd, state)
    safe_env = {key: value for key, value in os.environ.items() if not any(marker in key.upper() for marker in ("KEY", "TOKEN", "SECRET", "PASSWORD"))}
    injected = _resolved_credentials(payload.get("credential_env"))
    safe_env.update(injected)
    _emit_progress(progress_hook, f"{recipe or 'Terminal'} started: {executable}")
    process = subprocess.Popen(
        argv, cwd=cwd, env=safe_env, shell=False,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
    )
    lines: queue.Queue[str | None] = queue.Queue()

    def _read_output() -> None:
        try:
            if process.stdout is not None:
                for line in iter(process.stdout.readline, ""):
                    lines.put(line)
        finally:
            lines.put(None)

    threading.Thread(target=_read_output, name="smara-terminal-output", daemon=True).start()
    output_parts: list[str] = []
    reader_finished = False
    last_checkpoint = 0.0
    reported_output = False
    started = time.monotonic()
    try:
        while not reader_finished or process.poll() is None:
            try:
                line = lines.get(timeout=0.2)
                if line is None:
                    reader_finished = True
                else:
                    output_parts.append(line)
                    # Never stream command content to the hosted ledger. A
                    # local command may print personal data; the final result
                    # is still redacted for explicitly injected credentials.
                    if not reported_output:
                        _emit_progress(progress_hook, "Terminal output received locally")
                        reported_output = True
            except queue.Empty:
                pass
            now = time.monotonic()
            if now - started > MAX_COMMAND_SECONDS:
                _stop_process(process)
                raise RuntimeError(f"Terminal command exceeded {MAX_COMMAND_SECONDS} seconds.")
            if checkpoint is not None and now - last_checkpoint >= 1.0:
                last_checkpoint = now
                if checkpoint():
                    _stop_process(process)
                    raise ExecutionCancelled("Terminal execution was cancelled before completion.")
    finally:
        if process.poll() is None:
            _stop_process(process)
    output = "".join(output_parts)
    # A command can accidentally echo an injected token. Redact every known
    # value before anything is returned to the hosted task ledger or log.
    for secret in injected.values():
        if secret:
            output = output.replace(secret, "[REDACTED LOCAL CREDENTIAL]")
    changed_files = _git_status_files(cwd, state)
    artifacts = _collect_artifacts(payload, cwd, roots)
    _emit_progress(progress_hook, f"{recipe or 'Terminal'} finished with exit code {process.returncode}")
    result: dict[str, object] = {
        "action": "local_terminal", "argv": argv, "credential_env": sorted(injected),
        "exit_code": process.returncode, "output": output[:MAX_OUTPUT_CHARS],
        "recipe": recipe, "artifacts": artifacts,
    }
    if before_files is not None and changed_files is not None:
        result["changed_files"] = sorted(
            path for path, status in changed_files.items() if before_files.get(path) != status
        )
        result["changed_files_available"] = True
        result["workspace_changes_after"] = sorted(changed_files)
    else:
        result["changed_files"] = []
        result["changed_files_available"] = False
    return json.dumps(result, ensure_ascii=False)


def _allowed_browser_url(url: str, state: dict) -> tuple[str, str]:
    if not isinstance(url, str) or url.startswith("javascript:"):
        raise RuntimeError("local_browser requires a safe HTTP(S) URL.")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise RuntimeError("Only HTTP(S) browser URLs are allowed.")
    domains = state.get("browser_domains") or []
    hostname = parsed.hostname.rstrip(".").lower()
    allowed_domains = {
        item.strip().rstrip(".").lower().lstrip(".")
        for item in domains if isinstance(item, str) and item.strip()
    }
    if not allowed_domains or not any(hostname == item or hostname.endswith("." + item) for item in allowed_domains):
        raise RuntimeError("Browser URL is outside the configured desktop domain allowlist.")
    return url, hostname


_SIMPLE_DOM_SELECTOR = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*(?:[.#][A-Za-z0-9_-]+)?$")
_DOM_IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]+$")


def _dom_selector_matches(element: dict[str, object], selector: object) -> bool:
    """Match one deliberately small selector grammar, never arbitrary CSS."""
    if selector is None or selector == "":
        return True
    if not isinstance(selector, str) or len(selector) > 100 or not selector.strip():
        raise RuntimeError("selector must be a simple tag, #id, or .class selector.")
    value = selector.strip()
    if value.startswith("#") or value.startswith("."):
        if not _DOM_IDENTIFIER.fullmatch(value[1:]):
            raise RuntimeError("selector must be a simple tag, #id, or .class selector.")
    elif not _SIMPLE_DOM_SELECTOR.fullmatch(value):
        raise RuntimeError("selector must be a simple tag, #id, or .class selector.")
    tag = str(element.get("tag") or "")
    attrs = element.get("attributes")
    attributes = attrs if isinstance(attrs, dict) else {}
    if value.startswith("#"):
        return str(attributes.get("id") or "") == value[1:]
    if value.startswith("."):
        return value[1:] in str(attributes.get("class") or "").split()
    match = re.fullmatch(r"(?P<tag>[A-Za-z][A-Za-z0-9_-]*)(?:(?P<kind>[.#])(?P<name>[A-Za-z0-9_-]+))?", value)
    if not match:
        return False
    if tag != match.group("tag").lower():
        return False
    kind, name = match.group("kind"), match.group("name")
    if kind == "#":
        return str(attributes.get("id") or "") == name
    if kind == ".":
        return name in str(attributes.get("class") or "").split()
    return True


def _browser_result_path(path: Path, roots: list[Path]) -> str:
    for root in roots:
        try:
            return path.relative_to(root).as_posix()
        except ValueError:
            continue
    return path.name


def _fetch_browser_bytes(
    url: str,
    *,
    max_bytes: int,
    checkpoint=None,
    progress_hook=None,
) -> tuple[bytes, str]:
    """Fetch one public page/file without cookies, redirects, or credentials."""
    _emit_progress(progress_hook, "Fetching approved page without browser cookies")
    chunks: list[bytes] = []
    received = 0
    try:
        with httpx.Client(
            timeout=15,
            follow_redirects=False,
            headers={"User-Agent": "SmaraDesktop/0.1 local-inspection"},
        ) as client:
            with client.stream("GET", url) as response:
                if getattr(response, "is_redirect", False):
                    location = response.headers.get("location", "")
                    raise RuntimeError(
                        f"Page redirected; approve and inspect the destination separately ({str(location)[:300]})."
                    )
                response.raise_for_status()
                headers = response.headers
                content_type = str(headers.get("content-type", "")).lower()
                content_length = headers.get("content-length")
                try:
                    declared_length = int(content_length) if content_length is not None else None
                except (TypeError, ValueError):
                    declared_length = None
                if declared_length is not None and declared_length > max_bytes:
                    raise RuntimeError(f"Browser response exceeds the {max_bytes // (1024 * 1024)} MB local limit.")
                for chunk in response.iter_bytes():
                    if not isinstance(chunk, (bytes, bytearray)):
                        chunk = bytes(chunk)
                    received += len(chunk)
                    if received > max_bytes:
                        raise RuntimeError(f"Browser response exceeded the {max_bytes // (1024 * 1024)} MB local limit.")
                    chunks.append(bytes(chunk))
                    if checkpoint is not None and checkpoint():
                        raise ExecutionCancelled("Browser operation was cancelled before completion.")
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Could not fetch the approved page: {str(exc)[:300]}") from exc
    return b"".join(chunks), content_type


def _browser_download(payload: dict, roots: list[Path], state: dict, *, checkpoint=None, progress_hook=None) -> str:
    raw_destination = payload.get("destination") or payload.get("path")
    if not isinstance(raw_destination, str) or not raw_destination.strip():
        raise RuntimeError("Browser downloads require a destination inside an approved folder.")
    destination = Path(raw_destination).expanduser()
    if not destination.is_absolute():
        destination = roots[0] / destination
    target = _target(str(destination), roots, must_exist=False)
    if target.exists() or target.is_symlink():
        if target.is_symlink():
            raise RuntimeError("Symlinked download destinations are not allowed.")
        if target.is_dir():
            raise RuntimeError("Browser download destination must be a file path.")
        if payload.get("overwrite") is not True:
            raise RuntimeError("Download destination already exists; set overwrite=true to replace it.")
    overwrote = target.exists() and not target.is_symlink() and payload.get("overwrite") is True
    url, _hostname = _allowed_browser_url(payload.get("url"), state)
    temporary: Path | None = None
    try:
        _emit_progress(progress_hook, "Downloading approved file locally")
        with httpx.Client(
            timeout=30,
            follow_redirects=False,
            headers={"User-Agent": "SmaraDesktop/0.1 local-download"},
        ) as client:
            with client.stream("GET", url) as response:
                if getattr(response, "is_redirect", False):
                    location = response.headers.get("location", "")
                    raise RuntimeError(
                        f"Download redirected; approve and download the destination separately ({str(location)[:300]})."
                    )
                response.raise_for_status()
                content_type = str(response.headers.get("content-type", "application/octet-stream")).lower()
                content_length = response.headers.get("content-length")
                try:
                    declared_length = int(content_length) if content_length is not None else None
                except (TypeError, ValueError):
                    declared_length = None
                if declared_length is not None and declared_length > MAX_BROWSER_DOWNLOAD_BYTES:
                    raise RuntimeError("Browser download exceeds the 50 MB local limit.")
                with tempfile.NamedTemporaryFile("wb", dir=target.parent, delete=False) as handle:
                    temporary = Path(handle.name)
                    digest = hashlib.sha256()
                    received = 0
                    for chunk in response.iter_bytes():
                        if not isinstance(chunk, (bytes, bytearray)):
                            chunk = bytes(chunk)
                        received += len(chunk)
                        if received > MAX_BROWSER_DOWNLOAD_BYTES:
                            raise RuntimeError("Browser download exceeded the 50 MB local limit.")
                        handle.write(chunk)
                        digest.update(chunk)
                        if checkpoint is not None and checkpoint():
                            raise ExecutionCancelled("Browser download was cancelled before completion.")
                    handle.flush()
                    with contextlib.suppress(OSError):
                        os.fsync(handle.fileno())
                os.replace(temporary, target)
                temporary = None
        _emit_progress(progress_hook, "Browser download finished locally")
        return json.dumps({
            "action": "local_browser",
            "operation": "download",
            "url": url,
            "path": _browser_result_path(target, roots),
            "bytes_downloaded": received,
            "sha256": digest.hexdigest(),
            "content_type": content_type,
            "overwrote": overwrote,
            "proof": {"source_url": url, "content_sha256": digest.hexdigest(), "bytes": received},
        }, ensure_ascii=False)
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Could not download the approved file: {str(exc)[:300]}") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _browser(payload: dict, state: dict, roots: list[Path], *, checkpoint=None, progress_hook=None) -> str:
    url, _hostname = _allowed_browser_url(payload.get("url"), state)
    operation = payload.get("operation", "open")
    if operation == "open":
        if not webbrowser.open(url, new=0, autoraise=False):
            raise RuntimeError("The operating system did not accept the browser request.")
        return json.dumps({"action": "local_browser", "operation": "open", "url": url, "opened": True})
    if operation == "download":
        return _browser_download(payload, roots, state, checkpoint=checkpoint, progress_hook=progress_hook)
    if operation not in {"inspect_text", "inspect_dom"}:
        raise RuntimeError("local_browser supports open, inspect_text, inspect_dom, and download operations only.")
    max_bytes = MAX_BROWSER_INSPECT_BYTES
    raw_bytes, content_type = _fetch_browser_bytes(url, max_bytes=max_bytes, checkpoint=checkpoint, progress_hook=progress_hook)
    digest = hashlib.sha256(raw_bytes).hexdigest()
    raw = raw_bytes.decode("utf-8", errors="replace")
    if operation == "inspect_text":
        if not (content_type.startswith("text/") or "json" in content_type):
            raise RuntimeError("Page inspection accepts text and JSON responses only.")
        if "html" in content_type:
            extractor = _PageTextExtractor()
            extractor.feed(raw)
            text = "\n".join(extractor.parts)
            title = extractor.title
        else:
            text = raw
            title = ""
        result = {
            "action": "local_browser", "operation": operation, "url": url,
            "title": title, "content_type": content_type, "text": text[:MAX_BROWSER_TEXT_CHARS],
            "truncated": len(text) > MAX_BROWSER_TEXT_CHARS,
            "proof": {"source_url": url, "content_sha256": digest, "bytes": len(raw_bytes)},
        }
    else:
        if "html" not in content_type:
            raise RuntimeError("DOM inspection accepts HTML responses only.")
        selector = payload.get("selector")
        max_elements = _bounded_int(
            payload.get("max_elements"), default=MAX_BROWSER_DOM_ELEMENTS,
            minimum=1, maximum=MAX_BROWSER_DOM_ELEMENTS, field="max_elements",
        )
        extractor = _PageDomExtractor(
            semantic_only=selector in (None, ""),
            base_url=url,
        )
        extractor.feed(raw)
        matches = [element for element in extractor.elements if _dom_selector_matches(element, selector)]
        result = {
            "action": "local_browser", "operation": operation, "url": url,
            "title": extractor.title, "content_type": content_type,
            "selector": selector or None, "elements": matches[:max_elements],
            "count": len(matches),
            "truncated": len(matches) > max_elements or len(extractor.elements) >= MAX_BROWSER_DOM_SCAN_ELEMENTS,
            "proof": {"source_url": url, "content_sha256": digest, "bytes": len(raw_bytes)},
        }
    _emit_progress(progress_hook, "Page inspection finished locally")
    return json.dumps(result, ensure_ascii=False)


def execute_step(step: dict, state: dict, *, checkpoint=None, progress_hook=None) -> str:
    """Dispatch one leased step; never dispatch an undeclared capability."""
    if step.get("requires_approval"):
        raise RuntimeError("Desktop refused a step that has not passed Smara approval.")
    capability = step.get("required_capability")
    declared = set(state.get("capabilities") or DEFAULT_CAPABILITIES)
    if capability not in declared:
        raise RuntimeError("This desktop capability was not declared during pairing.")
    payload = step.get("executor_payload")
    if not isinstance(payload, dict):
        raise RuntimeError("The desktop step payload is invalid.")
    roots = _roots(state)
    if capability == "local_file_read":
        # Keep state out of the public payload schema while allowing the
        # read-only Git summary to enforce the configured git allowlist.
        inspect_payload = dict(payload)
        inspect_payload["_state"] = state
        return _workspace_inspect(inspect_payload, roots)
    if capability == "local_file_write":
        return _write_file(payload, roots, state)
    if capability == "local_terminal":
        return _terminal(payload, roots, state, checkpoint=checkpoint, progress_hook=progress_hook)
    if capability == "local_browser":
        return _browser(payload, state, roots, checkpoint=checkpoint, progress_hook=progress_hook)
    if capability == "local_integration":
        try:
            return execute_local_integration(payload, _resolved_credentials, checkpoint=checkpoint, progress_hook=progress_hook)
        except LocalIntegrationCancelled as exc:
            raise ExecutionCancelled(str(exc)) from exc
    raise RuntimeError(f"Desktop capability '{capability}' is not installed.")


@dataclass
class DesktopRunner:
    state_path: Path
    poll_seconds: float = 2.0
    heartbeat_seconds: float = 20.0
    _last_heartbeat: float = field(default=0.0, init=False)

    def run_once(self, client: httpx.Client, state: dict) -> bool:
        # Settings are written by the desktop UI while this long-lived
        # process is running. Reload the small scoped state file before every
        # poll so newly approved folders, terminal allowlists, and browser
        # domains take effect without requiring a manual executor restart.
        # Reloading is deliberately strict: if pairing state is removed or
        # corrupted, the executor must stop rather than continue with a stale
        # token or stale permissions.
        state = _load_state(self.state_path)
        # Used only for local undo snapshots; never persisted or sent to Smara.
        state["_state_path"] = str(self.state_path)
        now = time.monotonic()
        if now - self._last_heartbeat >= self.heartbeat_seconds:
            response = client.post(f"{state['smara_url']}/v1/executors/heartbeat", headers=_headers(state), json={"capabilities": state.get("capabilities", DEFAULT_CAPABILITIES)})
            response.raise_for_status()
            self._last_heartbeat = now
        response = client.post(f"{state['smara_url']}/v1/executors/claim", headers=_headers(state))
        response.raise_for_status()
        step = response.json().get("step")
        if not step:
            return False
        LOG.info("claimed step %s capability=%s", step.get("step_id"), step.get("required_capability"))

        def checkpoint() -> bool:
            """Refresh the lease while long local work runs; return cancel state."""
            response = client.post(
                f"{state['smara_url']}/v1/executors/steps/{step['step_id']}/heartbeat",
                headers=_headers(state),
            )
            response.raise_for_status()
            return bool(response.json().get("cancel_requested"))

        def progress(message: str) -> None:
            response = client.post(
                f"{state['smara_url']}/v1/executors/steps/{step['step_id']}/progress",
                headers=_headers(state), json={"message": message[:500]},
            )
            response.raise_for_status()

        try:
            result = execute_step(step, state, checkpoint=checkpoint, progress_hook=progress)
            # A desktop action can use most of its lease (for example a
            # bounded terminal command). Refresh it once before completion so
            # a delayed network response cannot finalize a lease another
            # executor has already recovered.
            final_heartbeat = client.post(
                f"{state['smara_url']}/v1/executors/steps/{step['step_id']}/heartbeat",
                headers=_headers(state),
            )
            final_heartbeat.raise_for_status()
            if final_heartbeat.json().get("cancel_requested"):
                raise ExecutionCancelled("The task was cancelled before the local result was recorded.")
            client.post(f"{state['smara_url']}/v1/executors/steps/{step['step_id']}/complete", headers=_headers(state), json={"result": result}).raise_for_status()
            LOG.info("completed step %s", step.get("step_id"))
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
            client.post(f"{state['smara_url']}/v1/executors/steps/{step['step_id']}/fail", headers=_headers(state), json={"error": str(exc)[:2_000]}).raise_for_status()
            LOG.warning("failed step %s: %s", step.get("step_id"), str(exc)[:300])
        return True

    def run_forever(self) -> None:
        state = _load_state(self.state_path)
        with httpx.Client(timeout=20, follow_redirects=False) as client:
            delay = self.poll_seconds
            while True:
                if _pause_path(self.state_path).exists():
                    time.sleep(min(max(self.poll_seconds, 1.0), 5.0))
                    continue
                try:
                    self.run_once(client, state)
                    delay = self.poll_seconds
                except KeyboardInterrupt:
                    return
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code in {401, 403}:
                        raise RuntimeError("Desktop credentials were rejected; pair this device again.") from exc
                    delay = min(delay * 2, 30)
                except httpx.HTTPError:
                    delay = min(delay * 2, 30)
                    LOG.warning("hosted service unavailable; retrying in %.1f seconds", delay)
                time.sleep(delay)


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Smara's outbound-only local executor")
    parser.add_argument("--api", default=os.getenv("SMARA_API_URL", "http://127.0.0.1:8080"))
    parser.add_argument("--pair", help="one-time pairing code from Smara Web or CLI")
    parser.add_argument("--state", type=Path, default=default_state_path())
    parser.add_argument("--allow-root", action="append", default=[], help="approved local folder; repeat as needed")
    parser.add_argument("--terminal-allow", action="append", default=[], help="allowed terminal executable basename; repeat as needed")
    parser.add_argument("--browser-domain", action="append", default=[], help="allowed browser domain; repeat as needed")
    parser.add_argument("--pair-only", action="store_true", help="pair and save state without starting the executor loop")
    parser.add_argument("--once", action="store_true", help="claim at most one step and exit")
    parser.add_argument("--pause", action="store_true", help="pause claims for the configured state")
    parser.add_argument("--resume", action="store_true", help="resume claims for the configured state")
    parser.add_argument("--revoke", action="store_true", help="revoke this paired desktop on the hosted service and remove local state")
    parser.add_argument("--status", action="store_true", help="print safe local executor status")
    parser.add_argument("--credential-list", action="store_true", help="list local credential names without values")
    parser.add_argument("--credential-set", help="save a local credential from stdin")
    parser.add_argument("--credential-provider", default="custom", help="provider label for --credential-set")
    parser.add_argument("--credential-get", help=argparse.SUPPRESS)
    parser.add_argument("--credential-delete", help="remove a local credential")
    parser.add_argument("--log", type=Path, default=default_log_path(), help="rotating desktop log path")
    args = parser.parse_args(argv)
    args.log.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(args.log, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    LOG.setLevel(logging.INFO)
    if not LOG.handlers:
        LOG.addHandler(handler)
    if args.pause:
        _pause_path(args.state).parent.mkdir(parents=True, exist_ok=True)
        _pause_path(args.state).write_text("paused\n", encoding="utf-8")
        print("Smara Desktop is paused. No new local work will be claimed.")
        return 0
    if args.credential_list:
        print(json.dumps(local_credential_summaries(), ensure_ascii=False))
        return 0
    if args.credential_get:
        # Parent-process IPC only; do not send this through the rotating log.
        print(resolve_local_credential(args.credential_get), end="")
        return 0
    if args.credential_set:
        save_local_credential(args.credential_set, sys.stdin.read().rstrip("\r\n"), args.credential_provider)
        print(json.dumps({"ok": True, "name": args.credential_set.strip().upper()}))
        return 0
    if args.credential_delete:
        print(json.dumps({"ok": True, "removed": delete_local_credential(args.credential_delete)}))
        return 0
    if args.resume:
        _pause_path(args.state).unlink(missing_ok=True)
        print("Smara Desktop is active.")
        return 0
    if args.revoke:
        state = _load_state(args.state)
        with httpx.Client(timeout=20, follow_redirects=False) as client:
            response = client.delete(f"{state['smara_url']}/v1/executors/{state['executor_id']}/self-revoke", headers=_headers(state))
            if response.status_code not in {200, 204}:
                response.raise_for_status()
        _pause_path(args.state).unlink(missing_ok=True)
        args.state.unlink(missing_ok=True)
        print("Smara Desktop was revoked and local pairing state was removed.")
        return 0
    if args.status:
        state = _load_state(args.state)
        print(json.dumps({
            "paired": True,
            "paused": _pause_path(args.state).exists(),
            "executor_id": state["executor_id"],
            "smara_url": state["smara_url"],
            "capabilities": state.get("capabilities", DEFAULT_CAPABILITIES),
            "allowed_roots": state.get("allowed_roots", []),
            "log": str(args.log),
        }, indent=2))
        return 0
    if args.pair:
        state = pair(args.api, args.pair, args.state, allowed_roots=args.allow_root)
        state["terminal_allowlist"] = args.terminal_allow
        state["browser_domains"] = args.browser_domain
        _save_state(args.state, state)
        print(f"Paired {state['executor_id']} with {state['smara_url']}; state saved to {args.state}")
        if args.pair_only:
            return 0
    if args.once:
        state = _load_state(args.state)
        with _single_runner(args.state), httpx.Client(timeout=20, follow_redirects=False) as client:
            if not _pause_path(args.state).exists():
                DesktopRunner(args.state).run_once(client, state)
        return 0
    with _single_runner(args.state):
        DesktopRunner(args.state).run_forever()
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        return _main(argv)
    except RuntimeError as exc:
        print(f"Smara Desktop: {exc}", file=sys.stderr)
        return 1
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in {401, 403}:
            message = "Desktop credentials were rejected; revoke the old desktop and pair this device again."
        else:
            message = f"Hosted Smara rejected the request (HTTP {exc.response.status_code})."
        print(f"Smara Desktop: {message}", file=sys.stderr)
        return 1
    except httpx.HTTPError:
        print("Smara Desktop: hosted Smara is temporarily unreachable; check the connection and try again.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
