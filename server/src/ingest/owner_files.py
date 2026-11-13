"""Files the owner sends (any type, up to 20 MB), kept in each shop's own file space.

The bytes live on disk, one folder per shop, never in the database. Each file is stored under a random token. The shop's
folder also holds index.json, which maps each token to the file's number, name, size, save time and deleted time, so the
owner can list and delete files by name. The agent sees a stored file as the file ref `local:<token>:<name>`, and its
file tools (read_attachment, inspect_and_import_file, run_python) open it through actions._fetch. Nothing here executes a file.

The agent has no delete function. Only the owner deletes, with a confirmation, through owner_file_commands. A deleted file
is hidden at once and can be restored for RESTORE_WINDOW (30 days); after that purge removes it for good. Numbers are never
reused. Each shop has a space of QUOTA_BYTES, counted over the files that are not deleted.

Files kept before the index existed are listed as "earlier file" and count toward the space like any other.
"""
import json
import os
import re
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.box.sandbox import safe_name
from src.ingest.media import MediaError

MAX_BYTES = 20 * 1024 * 1024          # Telegram's own download limit; the same cap on every channel
QUOTA_BYTES = 250 * 1024 * 1024       # each shop's file space during beta
RESTORE_WINDOW = timedelta(days=30)   # a deleted file can be restored this long, then it is removed for good
PURGE_EVERY_S = 3600
INDEX = "index.json"
EARLIER = "earlier file"
_TENANT = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)
_TOKEN = re.compile(r"[A-Za-z0-9_-]{16,64}")
GONE = "That file could not be found. Please send it again."
DELETED = "That file was deleted. Send 'restore <number>' within 30 days to get it back."
_lock = threading.Lock()              # index reads and writes, and the quota check with the write, happen one at a time
_last_purge = float("-inf")


class StoredFileGone(MediaError):
    """The reference does not belong to this shop, the file was deleted, or no such file exists."""


class StorageFull(Exception):
    """Saving or restoring this file would take the shop over its file space."""


def too_large_text(size_bytes: int) -> str:
    """The reply the owner gets for a file over the limit. Says the limit in plain words."""
    return (f"That file is {size_bytes / 1_000_000:.1f} MB, over the {MAX_BYTES // (1024 * 1024)} MB limit, so I cannot keep it. "
            "Please send a smaller copy.")


def full_text() -> str:
    """The reply the owner gets when their file space has no room left for this file."""
    return (f"Your file space ({QUOTA_BYTES // (1024 * 1024)} MB) has no room for this file, so I cannot keep it. "
            "Send 'files' to see what you have, and 'delete <number>' to free space.")


def _folder(base: str, tenant_id: str) -> Path:
    if not _TENANT.fullmatch(str(tenant_id)):
        raise ValueError("not a shop id")
    return Path(base) / str(tenant_id).lower()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dt(iso: str) -> datetime:
    return datetime.fromisoformat(iso)


def _write(folder: Path, idx: dict) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    tmp = folder / (INDEX + ".tmp")
    tmp.write_text(json.dumps(idx), encoding="utf-8")
    os.replace(tmp, folder / INDEX)


def _load(folder: Path) -> dict:
    """The shop's index. Files on disk the index does not know are added as earlier files; entries whose file is gone are
    dropped. Caller holds _lock."""
    try:
        idx = json.loads((folder / INDEX).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        idx = {"next": 1, "files": {}}
    files = idx.setdefault("files", {})
    on_disk = {f.name: f for f in folder.iterdir() if f.is_file() and f.name not in (INDEX, INDEX + ".tmp")} \
        if folder.is_dir() else {}
    changed = False
    for token in [t for t in files if t not in on_disk]:
        del files[token]
        changed = True
    for token, f in on_disk.items():
        if token not in files:
            st = f.stat()
            files[token] = {"no": idx["next"], "name": EARLIER, "bytes": st.st_size,
                            "saved": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(), "deleted": None}
            idx["next"] += 1
            changed = True
    if changed:
        _write(folder, idx)
    return idx


def _live_bytes(idx: dict) -> int:
    return sum(e["bytes"] for e in idx["files"].values() if e["deleted"] is None)


def _view(e: dict, now: datetime) -> dict:
    out = {"no": e["no"], "name": e["name"], "bytes": e["bytes"], "saved": _dt(e["saved"])}
    if e["deleted"]:
        out["days_left"] = max(0, (RESTORE_WINDOW - (now - _dt(e["deleted"]))).days)
    return out


def used_bytes(base: str, tenant_id: str) -> int:
    """How much of the shop's file space its kept (not deleted) files take."""
    folder = _folder(base, tenant_id)
    with _lock:
        return _live_bytes(_load(folder))


def save(base: str, tenant_id: str, filename: str, data: bytes) -> str:
    """Store one file for a shop. Returns the file ref the agent uses. Raises StorageFull if the space has no room."""
    if len(data) > MAX_BYTES:
        raise ValueError("file over the limit")
    folder = _folder(base, tenant_id)
    token = secrets.token_urlsafe(12)
    name = safe_name(filename)
    with _lock:
        idx = _load(folder)
        if _live_bytes(idx) + len(data) > QUOTA_BYTES:
            raise StorageFull(full_text())
        folder.mkdir(parents=True, exist_ok=True)
        (folder / token).write_bytes(data)
        idx["files"][token] = {"no": idx["next"], "name": name, "bytes": len(data), "saved": _now_iso(), "deleted": None}
        idx["next"] += 1
        _write(folder, idx)
    return f"local:{token}:{name}"


def read(base: str, tenant_id: str, token: str) -> bytes:
    """A stored file, only for the shop that owns it. A foreign, unknown or deleted reference raises StoredFileGone."""
    if not _TOKEN.fullmatch(token or ""):
        raise StoredFileGone(GONE)
    try:
        folder = _folder(base, tenant_id)
    except ValueError:
        raise StoredFileGone(GONE) from None
    with _lock:
        entry = _load(folder)["files"].get(token)
    if entry is None:
        raise StoredFileGone(GONE)
    if entry["deleted"]:
        raise StoredFileGone(DELETED)
    try:
        return (folder / token).read_bytes()
    except FileNotFoundError:
        raise StoredFileGone(GONE) from None


def live_files(base: str, tenant_id: str, now: datetime) -> list[dict]:
    """The shop's files that are not deleted, by number."""
    folder = _folder(base, tenant_id)
    with _lock:
        files = _load(folder)["files"].values()
        return sorted((_view(e, now) for e in files if e["deleted"] is None), key=lambda v: v["no"])


def deleted_files(base: str, tenant_id: str, now: datetime) -> list[dict]:
    """The shop's deleted files that can still be restored, by number."""
    folder = _folder(base, tenant_id)
    with _lock:
        files = _load(folder)["files"].values()
        return sorted((_view(e, now) for e in files
                       if e["deleted"] and now - _dt(e["deleted"]) < RESTORE_WINDOW), key=lambda v: v["no"])


def delete(base: str, tenant_id: str, no: int, now: datetime) -> dict | None:
    """Hide a file from the owner and from the agent. It can be restored for RESTORE_WINDOW. None if there is no such file."""
    folder = _folder(base, tenant_id)
    with _lock:
        idx = _load(folder)
        for e in idx["files"].values():
            if e["no"] == no and e["deleted"] is None:
                e["deleted"] = now.isoformat()
                _write(folder, idx)
                return _view(e, now)
    return None


def restore(base: str, tenant_id: str, no: int, now: datetime) -> dict | None:
    """Bring back a file deleted within RESTORE_WINDOW. None if it is not in the deleted list.
    Raises StorageFull if the space no longer has room for it."""
    folder = _folder(base, tenant_id)
    with _lock:
        idx = _load(folder)
        for e in idx["files"].values():
            if e["no"] == no and e["deleted"] and now - _dt(e["deleted"]) < RESTORE_WINDOW:
                if _live_bytes(idx) + e["bytes"] > QUOTA_BYTES:
                    raise StorageFull("There is no room to restore that file. Delete something first, then send 'restore' again.")
                e["deleted"] = None
                _write(folder, idx)
                return _view(e, now)
    return None


def purge(base: str, now: datetime) -> int:
    """Remove for good every file deleted more than RESTORE_WINDOW ago. Returns how many were removed."""
    root = Path(base)
    if not root.is_dir():
        return 0
    removed = 0
    with _lock:
        for folder in root.iterdir():
            if not folder.is_dir() or not _TENANT.fullmatch(folder.name):
                continue
            idx = _load(folder)
            gone = [t for t, e in idx["files"].items() if e["deleted"] and now - _dt(e["deleted"]) >= RESTORE_WINDOW]
            for token in gone:
                (folder / token).unlink(missing_ok=True)
                del idx["files"][token]
                removed += 1
            if gone:
                _write(folder, idx)
    return removed


def purge_due(base: str, now: datetime) -> int:
    """purge, at most once an hour (the scheduler ticks every minute)."""
    global _last_purge
    if time.monotonic() - _last_purge < PURGE_EVERY_S:
        return 0
    _last_purge = time.monotonic()
    return purge(base, now)
