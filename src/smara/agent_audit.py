"""Local audit log of everything an agent does on this PC: browser steps, app control, approvals, downloads.

Kept only on this PC, under %APPDATA%\\Smara\\audit (override with SMARA_AUDIT_DIR), one JSON line per step in a
file per day and source (``2026-11-18.desktop.jsonl``, ``2026-11-18.bridge.jsonl``). Screenshots that belong to a
step sit next to it under ``shots/<day>/``. The owner can list and delete any day or everything; nothing here is
ever uploaded. Values are scrubbed of secrets before they are written, and nothing typed into a password, OTP,
PIN or card box is stored (those steps are refused before they run, but the log never relies on that).

The bridge helper (smara_bridge.py) is a standalone script and writes the same format with its own small writer;
keep FIELDS in step with it.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
from pathlib import Path
import re
import shutil
import threading
import time
import uuid

FIELDS = ("id", "ts", "source", "task", "kind", "action", "target", "detail", "outcome", "approval", "screenshot")
SOURCES = ("desktop", "bridge")
KEEP_DAYS = 30
MAX_TEXT = 600
MAX_DETAIL = 2000
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_lock = threading.Lock()

_SECRET = re.compile(
    r"\b(?:sk_mem_|sk-|sk_|tvly-|ghp_|github_pat_|xox[abp]-)[A-Za-z0-9_-]{12,}"
    r"|(?i:bearer)\s+[A-Za-z0-9._~-]{12,}"
    r"|-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----", re.S)
_ASSIGNMENT = re.compile(r"(?i)\b([A-Za-z0-9_-]{0,40}(?:api[_-]?key|access[_-]?token|secret|password|passwd|otp|upi ?pin|cvv)\s*[=:]\s*)[^\s,;\"']+")
_SECRET_FIELD = re.compile(r"(?i)pass(word|code)?|\botp\b|one.?time|\bpin\b|upi.?pin|cvv|cvc|card.?(number|no)|secret|token")
_DIGITS = re.compile(r"(?:\d[ -]?){13,19}")


def audit_dir() -> Path:
    if os.environ.get("SMARA_AUDIT_DIR"):
        return Path(os.environ["SMARA_AUDIT_DIR"])
    return Path(os.environ.get("APPDATA", str(Path.home() / ".config"))) / "Smara" / "audit"


def _luhn(digits: str) -> bool:
    total, odd = 0, True
    for ch in reversed(digits):
        n = int(ch)
        if not odd:
            n = n * 2 - 9 if n > 4 else n * 2
        total, odd = total + n, not odd
    return total % 10 == 0


def scrub(text) -> str:
    """Remove keys, tokens, assignments of secrets and card-like numbers from a piece of text."""
    text = "" if text is None else str(text)
    text = _SECRET.sub("[secret removed]", text)
    text = _ASSIGNMENT.sub(lambda m: m.group(1) + "[removed]", text)

    def card(m):
        digits = re.sub(r"\D", "", m.group(0))
        return "[card number removed]" if 13 <= len(digits) <= 19 and _luhn(digits) else m.group(0)
    return _DIGITS.sub(card, text)


def scrub_value(value, field_hint: str = "", depth: int = 0):
    """Scrub a JSON-like value; anything whose key looks like a secret field is replaced outright."""
    if depth > 4:
        return "…"
    if isinstance(value, dict):
        out = {}
        for key, item in list(value.items())[:40]:
            key = str(key)[:60]
            out[key] = "[removed]" if _SECRET_FIELD.search(key) and isinstance(item, (str, int)) else scrub_value(item, key, depth + 1)
        return out
    if isinstance(value, list):
        return [scrub_value(item, field_hint, depth + 1) for item in value[:40]]
    if isinstance(value, str):
        return "[removed]" if field_hint and _SECRET_FIELD.search(field_hint) else scrub(value)[:MAX_TEXT]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return scrub(str(value))[:MAX_TEXT]


def _today() -> str:
    return _dt.date.today().isoformat()


def _file(day: str, source: str) -> Path:
    return audit_dir() / f"{day}.{source}.jsonl"


def record(source: str, kind: str, action: str, *, task: str = "", target: str = "", detail=None, outcome: str = "",
           approval: str = "", screenshot: bytes | str | Path | None = None) -> dict:
    """Append one step. Never raises: an audit failure must not break the agent, but it is reported in the entry."""
    if source not in SOURCES:
        raise ValueError("unknown audit source")
    entry = {"id": uuid.uuid4().hex[:16], "ts": round(time.time(), 6), "source": source, "task": str(task)[:80],
             "kind": str(kind)[:30], "action": str(action)[:60], "target": scrub(target)[:MAX_TEXT],
             "detail": scrub_value(detail) if detail is not None else None, "outcome": scrub(outcome)[:MAX_TEXT],
             "approval": str(approval)[:20], "screenshot": ""}
    encoded = json.dumps(entry["detail"], ensure_ascii=False)
    if len(encoded) > MAX_DETAIL:
        entry["detail"] = encoded[:MAX_DETAIL] + "…"
    day = _today()
    try:
        if screenshot is not None:
            entry["screenshot"] = _store_shot(day, entry["id"], screenshot)
        line = json.dumps(entry, ensure_ascii=False) + "\n"
        with _lock:
            audit_dir().mkdir(parents=True, exist_ok=True)
            with open(_file(day, source), "a", encoding="utf-8") as handle:
                handle.write(line)
        _prune_later()
    except OSError as exc:
        entry["outcome"] = (entry["outcome"] + f" (audit not saved: {type(exc).__name__})").strip()
    return entry


SHOTS_PER_DAY_BYTES = 500_000_000


def _store_shot(day: str, identity: str, screenshot) -> str:
    folder = audit_dir() / "shots" / day
    folder.mkdir(parents=True, exist_ok=True)
    if sum(p.stat().st_size for p in folder.iterdir() if p.is_file()) > SHOTS_PER_DAY_BYTES:
        return ""                         # a very busy day: the step is still logged, without its picture
    target = folder / f"{identity}.png"
    if isinstance(screenshot, (bytes, bytearray)):
        target.write_bytes(bytes(screenshot))
    else:
        shutil.copyfile(Path(screenshot), target)
    return f"shots/{day}/{identity}.png"


_last_prune = 0.0


def _prune_later() -> None:
    global _last_prune
    if time.monotonic() - _last_prune > 3600:
        _last_prune = time.monotonic()
        prune()


def prune(keep_days: int = KEEP_DAYS) -> int:
    """Delete days older than keep_days. Returns how many days were removed."""
    cutoff = (_dt.date.today() - _dt.timedelta(days=keep_days)).isoformat()
    return sum(delete(day) for day in [d["day"] for d in days()] if day < cutoff)


def days() -> list[dict]:
    root = audit_dir()
    if not root.is_dir():
        return []
    found: dict[str, dict] = {}
    for path in root.glob("*.jsonl"):
        day = path.name.split(".", 1)[0]
        if not _DAY.match(day):
            continue
        row = found.setdefault(day, {"day": day, "entries": 0, "bytes": 0})
        try:
            with open(path, encoding="utf-8") as handle:
                row["entries"] += sum(1 for line in handle if line.strip())
            row["bytes"] += path.stat().st_size
        except OSError:
            continue
    return sorted(found.values(), key=lambda row: row["day"], reverse=True)


def entries(day: str | None = None, limit: int = 200, source: str | None = None, task: str | None = None,
            kind: str | None = None) -> list[dict]:
    """Newest first."""
    day = day or _today()
    if not _DAY.match(day):
        raise ValueError("day must be YYYY-MM-DD")
    rows = []
    for name in (source,) if source else SOURCES:
        path = _file(day, name)
        if not path.is_file():
            continue
        with open(path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict) and (not task or row.get("task") == task) and (not kind or row.get("kind") == kind):
                    rows.append((row.get("ts") or 0, len(rows), {key: row.get(key) for key in FIELDS}))
    rows.sort(key=lambda item: item[:2], reverse=True)          # same millisecond: the later line is newer
    return [row for _, _, row in rows[:max(1, min(int(limit), 2000))]]


def screenshot_path(relative: str) -> Path:
    """Resolve a step's screenshot (PNG from app control, JPEG from the browser), refusing anything outside the audit folder."""
    root = audit_dir().resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root / "shots") or path.suffix.lower() not in (".png", ".jpg"):
        raise ValueError("not an audit screenshot")
    return path


def delete(day: str | None = None) -> int:
    """Delete one day (its log files and screenshots) or, with day=None, the whole audit log. Returns 1/0 per day."""
    root = audit_dir()
    if day is None:
        if root.is_dir():
            with _lock:
                shutil.rmtree(root, ignore_errors=True)
        return 1
    if not _DAY.match(day):
        raise ValueError("day must be YYYY-MM-DD")
    removed = 0
    with _lock:
        for path in root.glob(f"{day}.*.jsonl"):
            path.unlink(missing_ok=True)
            removed = 1
        shots = root / "shots" / day
        if shots.is_dir():
            shutil.rmtree(shots, ignore_errors=True)
            removed = 1
    return removed


# ---- Desktop runtime hook ---------------------------------------------------------------------------------------
BROWSER_SERVERS = {"playwright", "smara_browser"}
COMPUTER_SERVERS = {"smara_computer"}
# Servers that write their own, richer entries (window, screenshot): the stream hook does not repeat them.
SELF_AUDITED = {"smara_computer"}
# Steps that can start a download (all of them ask the owner first). Screenshots and snapshots the browser tool
# writes to the same folder are its own output files, not downloads.
DOWNLOAD_TRIGGERS = {"browser_click", "browser_press_key", "browser_type", "browser_fill_form", "browser_select_option",
                     "browser_drop", "browser_navigate", "browser_act", "browser_download"}
NOT_DOWNLOADS = {".yml", ".yaml", ".md", ".log"}


def kind_of(server: str) -> str:
    return "browser" if server in BROWSER_SERVERS else "computer" if server in COMPUTER_SERVERS else "tool"


def _target(arguments) -> str:
    if not isinstance(arguments, dict):
        return ""
    for key in ("url", "element", "ref", "window_id", "name", "path", "tab_id", "key", "selector"):
        if arguments.get(key) not in (None, ""):
            return f"{key}={arguments[key]}"
    return ""


def _result_text(item: dict) -> str:
    if item.get("error"):
        error = item["error"]
        return "error: " + str(error.get("message") if isinstance(error, dict) else error)
    result = item.get("result")
    if isinstance(result, dict):
        parts = [block.get("text", "") for block in result.get("content") or [] if isinstance(block, dict) and block.get("type") == "text"]
        return " ".join(parts)[:300]
    return ""


class RuntimeAuditor:
    """Watches the native JSON-RPC stream (both directions) and records tool calls and approval answers.

    It only reads messages; it never changes, delays or drops one. Downloads saved by the chat browser land in
    ``download_dir``; new files there are recorded after each browser step.
    """

    def __init__(self, download_dir: Path | None = None, enabled: bool = True, task: str = ""):
        self.enabled = enabled
        self.task = task          # a background task's id; a chat uses the conversation's thread id
        self.download_dir = Path(download_dir) if download_dir else None
        self.pending: dict[str, dict] = {}
        self.seen_files = self._files()

    def _files(self) -> set[str]:
        if not self.download_dir or not self.download_dir.is_dir():
            return set()
        try:
            return {p.name for p in self.download_dir.iterdir() if p.is_file() and p.suffix.lower() not in NOT_DOWNLOADS}
        except OSError:
            return set()

    def from_runtime(self, message: dict) -> None:
        if not self.enabled or not isinstance(message, dict):
            return
        try:
            method = message.get("method")
            params = message.get("params") or {}
            if method == "mcpServer/elicitation/request" and "id" in message:
                self.pending[json.dumps(message["id"])] = {"server": params.get("serverName", ""), "message": params.get("message", ""),
                                                           "task": self.task or params.get("threadId", "")}
            elif method == "item/completed":
                item = params.get("item") or {}
                if item.get("type") == "mcpToolCall" and str(item.get("server", "")) not in SELF_AUDITED:
                    server, tool = str(item.get("server", "")), str(item.get("tool", ""))
                    record("desktop", kind_of(server), tool, task=self.task or params.get("threadId", ""), target=_target(item.get("arguments")),
                           detail={"server": server, "arguments": item.get("arguments"), "ms": item.get("durationMs")},
                           outcome=f"{item.get('status', '')} {_result_text(item)}".strip())
                    if server in BROWSER_SERVERS:
                        self._new_downloads(self.task or params.get("threadId", ""), report=tool in DOWNLOAD_TRIGGERS)
        except Exception:  # noqa: BLE001 - the audit must never break the chat
            pass

    def from_owner(self, message: dict) -> None:
        if not self.enabled or not isinstance(message, dict) or message.get("method") or "id" not in message:
            return
        try:
            asked = self.pending.pop(json.dumps(message["id"]), None)
            if asked is None:
                return
            action = (message.get("result") or {}).get("action") or ("error" if message.get("error") else "")
            record("desktop", "approval", "approved" if action == "accept" else "declined", task=asked["task"],
                   target=asked["server"], detail={"asked": asked["message"]}, approval="owner")
        except Exception:  # noqa: BLE001
            pass

    def _new_downloads(self, task: str, report: bool = True) -> None:
        now = self._files()
        for name in sorted(now - self.seen_files) if report else ():
            path = self.download_dir / name
            size = path.stat().st_size if path.exists() else 0
            record("desktop", "browser", "download_saved", task=task, target=name,
                   detail={"folder": str(self.download_dir), "bytes": size}, outcome="saved after an approved click")
        self.seen_files = now


# ---- Settings / UI operation -------------------------------------------------------------------------------------
MAX_SHOT_BYTES = 4_000_000


def manage(request: dict) -> dict:
    """operation "audit": days | list | shot | delete. Only the owner's Desktop UI calls this."""
    action = request.get("action", "days")
    if set(request) - {"operation", "action", "day", "kind", "source", "task", "limit", "path", "all"}:
        raise ValueError("Unexpected audit request fields")
    if action == "days":
        return {"days": days(), "folder": str(audit_dir()), "keep_days": KEEP_DAYS}
    if action == "list":
        source = request.get("source") or None
        if source is not None and source not in SOURCES:
            raise ValueError("Unknown audit source")
        return {"entries": entries(request.get("day") or None, int(request.get("limit") or 200), source,
                                   request.get("task") or None, request.get("kind") or None)}
    if action == "shot":
        import base64
        path = screenshot_path(str(request.get("path") or ""))
        if not path.is_file() or path.stat().st_size > MAX_SHOT_BYTES:
            raise ValueError("Screenshot is missing or too large to show")
        return {"png_base64": base64.b64encode(path.read_bytes()).decode("ascii")}
    if action == "delete":
        if request.get("all") is True:
            delete(None)
            return {"deleted": "all", "days": []}
        day = request.get("day")
        if not isinstance(day, str):
            raise ValueError("Choose a day to delete, or all")
        delete(day)
        return {"deleted": day, "days": days()}
    raise ValueError("Unsupported audit action")
