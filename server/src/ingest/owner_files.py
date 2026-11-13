"""Files the owner sends (any type, up to 20 MB), kept permanently in each shop's own file space.

The bytes live on disk, one folder per shop, never in the database. Each file is named by a random token, so the
folder layout reveals no names. The agent sees a stored file as the file ref `local:<token>:<name>`, and its file tools
(read_attachment, inspect_and_import_file, run_python) open it through actions._fetch. Nothing here executes a file.

Files are never deleted by a timer and never overwritten: every save writes a new token. The agent has no delete
function. Each shop has a space of QUOTA_BYTES; a save that would go over it is refused with a plain message.
"""
import re
import secrets
import threading
from pathlib import Path

from src.box.sandbox import safe_name
from src.ingest.media import MediaError

MAX_BYTES = 20 * 1024 * 1024          # Telegram's own download limit; the same cap on every channel
QUOTA_BYTES = 250 * 1024 * 1024       # each shop's permanent file space during beta
_TENANT = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)
_TOKEN = re.compile(r"[A-Za-z0-9_-]{16,64}")
GONE = "That file could not be found. Please send it again."
_save_lock = threading.Lock()         # the quota check and the write happen together, one save at a time


class StoredFileGone(MediaError):
    """The reference does not belong to this shop, or no such file exists."""


class StorageFull(Exception):
    """Saving this file would take the shop over its file space."""


def too_large_text(size_bytes: int) -> str:
    """The reply the owner gets for a file over the limit. Says the limit in plain words."""
    return (f"That file is {size_bytes / 1_000_000:.1f} MB, over the {MAX_BYTES // (1024 * 1024)} MB limit, so I cannot keep it. "
            "Please send a smaller copy.")


def full_text() -> str:
    """The reply the owner gets when their file space has no room left for this file."""
    return (f"Your file space ({QUOTA_BYTES // (1024 * 1024)} MB) has no room for this file, so I cannot keep it. "
            "Please send a smaller file, or ask us to make room.")


def _folder(base: str, tenant_id: str) -> Path:
    if not _TENANT.fullmatch(str(tenant_id)):
        raise ValueError("not a shop id")
    return Path(base) / str(tenant_id).lower()


def used_bytes(base: str, tenant_id: str) -> int:
    """How much of the shop's file space its kept files take."""
    folder = _folder(base, tenant_id)
    if not folder.is_dir():
        return 0
    return sum(f.stat().st_size for f in folder.iterdir() if f.is_file())


def save(base: str, tenant_id: str, filename: str, data: bytes) -> str:
    """Store one file for a shop. Returns the file ref the agent uses. Raises StorageFull if the space has no room."""
    if len(data) > MAX_BYTES:
        raise ValueError("file over the limit")
    folder = _folder(base, tenant_id)
    with _save_lock:
        if used_bytes(base, tenant_id) + len(data) > QUOTA_BYTES:
            raise StorageFull(full_text())
        folder.mkdir(parents=True, exist_ok=True)
        token = secrets.token_urlsafe(12)
        (folder / token).write_bytes(data)
    return f"local:{token}:{safe_name(filename)}"


def read(base: str, tenant_id: str, token: str) -> bytes:
    """A stored file, only for the shop that owns it. A foreign or unknown reference raises StoredFileGone."""
    if not _TOKEN.fullmatch(token or ""):
        raise StoredFileGone(GONE)
    try:
        return (_folder(base, tenant_id) / token).read_bytes()
    except (FileNotFoundError, ValueError):
        raise StoredFileGone(GONE) from None
