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
import hashlib
import json
import logging
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
import webbrowser
from dataclasses import dataclass, field
from logging.handlers import RotatingFileHandler
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx


MAX_FILE_BYTES = 256 * 1024
MAX_OUTPUT_CHARS = 32_000
MAX_COMMAND_SECONDS = 60
DEFAULT_CAPABILITIES = ["local_file_read"]
STATE_ENV = "SMARA_DESKTOP_STATE"
CREDENTIALS_ENV = "SMARA_DESKTOP_CREDENTIALS"
LOG = logging.getLogger("smara.desktop")
_CREDENTIAL_NAME = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")


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


def _write_file(payload: dict, roots: list[Path]) -> str:
    target = _target(payload.get("path"), roots, must_exist=False)
    content = payload.get("content")
    if not isinstance(content, str):
        raise RuntimeError("local_file_write requires text content.")
    data = content.encode("utf-8")
    if len(data) > MAX_FILE_BYTES:
        raise RuntimeError(f"Written content exceeds the {MAX_FILE_BYTES} byte limit.")
    if target.exists() and target.is_symlink():
        raise RuntimeError("Symlinked files are not allowed.")
    mode = "a" if payload.get("append") is True else "w"
    if mode == "a":
        with target.open("ab") as handle:
            if handle.tell() + len(data) > MAX_FILE_BYTES:
                raise RuntimeError("Appended content exceeds the file size limit.")
            handle.write(data)
    else:
        # Atomic replacement keeps a crash from leaving a partial file.
        with tempfile.NamedTemporaryFile("wb", dir=target.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(data)
        try:
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    return json.dumps({"action": "local_file_write", "file_name": target.name, "bytes_written": len(data), "sha256": hashlib.sha256(data).hexdigest()})


def _terminal(payload: dict, roots: list[Path], state: dict) -> str:
    raw = payload.get("argv")
    if isinstance(raw, list) and all(isinstance(item, str) for item in raw):
        argv = [item for item in raw if item]
    elif isinstance(payload.get("command"), str):
        command = payload["command"]
        if any(char in command for char in "|&;><`\n\r"):
            raise RuntimeError("Shell operators are not allowed; provide an argv list instead.")
        argv = shlex.split(command, posix=os.name != "nt")
    else:
        raise RuntimeError("local_terminal requires an argv list or bounded command.")
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
    safe_env = {key: value for key, value in os.environ.items() if not any(marker in key.upper() for marker in ("KEY", "TOKEN", "SECRET", "PASSWORD"))}
    injected = _resolved_credentials(payload.get("credential_env"))
    safe_env.update(injected)
    try:
        completed = subprocess.run(argv, cwd=cwd, env=safe_env, shell=False, capture_output=True, text=True, timeout=MAX_COMMAND_SECONDS, check=False)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Terminal command exceeded {MAX_COMMAND_SECONDS} seconds.") from exc
    output = (completed.stdout or "") + ("\n" + completed.stderr if completed.stderr else "")
    # A command can accidentally echo an injected token. Redact every known
    # value before anything is returned to the hosted task ledger or log.
    for secret in injected.values():
        if secret:
            output = output.replace(secret, "[REDACTED LOCAL CREDENTIAL]")
    return json.dumps({"action": "local_terminal", "argv": argv, "credential_env": sorted(injected), "exit_code": completed.returncode, "output": output[:MAX_OUTPUT_CHARS]}, ensure_ascii=False)


def _browser(payload: dict, state: dict) -> str:
    url = payload.get("url")
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
    if not webbrowser.open(url, new=0, autoraise=False):
        raise RuntimeError("The operating system did not accept the browser request.")
    return json.dumps({"action": "local_browser", "url": url, "opened": True})


def execute_step(step: dict, state: dict) -> str:
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
        return _read_file(payload, roots)
    if capability == "local_file_write":
        return _write_file(payload, roots)
    if capability == "local_terminal":
        return _terminal(payload, roots, state)
    if capability == "local_browser":
        return _browser(payload, state)
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
        try:
            result = execute_step(step, state)
            # A desktop action can use most of its lease (for example a
            # bounded terminal command). Refresh it once before completion so
            # a delayed network response cannot finalize a lease another
            # executor has already recovered.
            client.post(
                f"{state['smara_url']}/v1/executors/steps/{step['step_id']}/heartbeat",
                headers=_headers(state),
            ).raise_for_status()
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
