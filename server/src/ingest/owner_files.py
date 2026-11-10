"""Files the owner sends (any type, up to 20 MB), kept on this server for 7 days.

The bytes live on disk, one folder per shop, never in the database. Each file is named by a random token, so the
folder layout reveals no names. The agent sees a stored file as the file ref `local:<token>:<name>`, and its file tools
(read_attachment, inspect_and_import_file, run_python) open it through actions._fetch. Nothing here executes a file.
Retention is by file age: `purge_due` deletes files older than RETENTION, once an hour from the scheduler.
"""
import re
import secrets
import time
from datetime import datetime, timedelta
from pathlib import Path

from src.box.sandbox import safe_name
from src.ingest.media import MediaError

MAX_BYTES = 20 * 1024 * 1024          # Telegram's own download limit; the same cap on every channel
RETENTION = timedelta(days=7)
PURGE_EVERY_S = 3600
_TENANT = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)
_TOKEN = re.compile(r"[A-Za-z0-9_-]{16,64}")
GONE = "That file is no longer kept (files are kept for 7 days). Please send it again."
_last_purge = float("-inf")


class StoredFileGone(MediaError):
    """The file was purged, or the reference does not belong to this shop."""


def too_large_text(size_bytes: int) -> str:
    """The reply the owner gets for a file over the limit. Says the limit in plain words."""
    return (f"That file is {size_bytes / 1_000_000:.1f} MB, over the {MAX_BYTES // (1024 * 1024)} MB limit, so I cannot keep it. "
            "Please send a smaller copy.")


def _folder(base: str, tenant_id: str) -> Path:
    if not _TENANT.fullmatch(str(tenant_id)):
        raise ValueError("not a shop id")
    return Path(base) / str(tenant_id).lower()


def save(base: str, tenant_id: str, filename: str, data: bytes) -> str:
    """Store one file for a shop. Returns the file ref the agent uses."""
    if len(data) > MAX_BYTES:
        raise ValueError("file over the limit")
    folder = _folder(base, tenant_id)
    folder.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(12)
    (folder / token).write_bytes(data)
    return f"local:{token}:{safe_name(filename)}"


def read(base: str, tenant_id: str, token: str) -> bytes:
    """A stored file, only for the shop that owns it. Purged or foreign references raise StoredFileGone."""
    if not _TOKEN.fullmatch(token or ""):
        raise StoredFileGone(GONE)
    try:
        return (_folder(base, tenant_id) / token).read_bytes()
    except (FileNotFoundError, ValueError):
        raise StoredFileGone(GONE) from None


def purge(base: str, now: datetime) -> int:
    """Delete every stored file older than RETENTION. Returns how many were deleted."""
    root = Path(base)
    if not root.is_dir():
        return 0
    cutoff = now.timestamp() - RETENTION.total_seconds()
    deleted = 0
    for folder in root.iterdir():
        if not folder.is_dir() or not _TENANT.fullmatch(folder.name):
            continue
        for f in folder.iterdir():
            if f.is_file() and f.stat().st_mtime < cutoff:
                f.unlink(missing_ok=True)
                deleted += 1
    return deleted


def purge_due(base: str, now: datetime) -> int:
    """purge, at most once an hour (the scheduler ticks every minute)."""
    global _last_purge
    if time.monotonic() - _last_purge < PURGE_EVERY_S:
        return 0
    _last_purge = time.monotonic()
    return purge(base, now)
