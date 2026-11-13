"""The owner's commands for their file space. Plain text, so they work the same on WhatsApp and Telegram:

  files                 list the files kept, by number, with the space used
  delete 3              ask first: shows file 3 and the words to confirm
  yes delete 3          delete file 3 (it can be restored for 30 days)
  deleted files         list deleted files that can still be restored
  restore 3             bring back file 3 if it was deleted within 30 days

The agent has no such tools. Only these commands delete, and only after the owner has sent the number.
"""
import asyncio
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from src.ingest import owner_files

IST = ZoneInfo("Asia/Kolkata")
LIST_MAX = 30
_LIST = ("files", "my files", "file list", "meri files", "files list")
_DELETED = ("deleted files", "deleted", "deleted list")
_CONFIRM = re.compile(r"(?:yes|haan|ha)\s+(?:delete|hatao)\s+(?:file\s+)?#?(\d+)")
_DELETE = re.compile(r"(?:delete|hatao)\s+(?:file\s+)?#?(\d+)")
_RESTORE = re.compile(r"restore\s+(?:file\s+)?#?(\d+)")
_PUNCT = re.compile(r"[,.!?;:'\"।]")


def _mb(n: int) -> str:
    return f"{n / (1024 * 1024):.1f} MB"


def _when(dt: datetime) -> str:
    return dt.astimezone(IST).strftime("%d %b")


def _list_text(base: str, tenant_id: str, now: datetime) -> str:
    files = owner_files.live_files(base, tenant_id, now)
    if not files:
        return "You have no files kept yet. Send a file and I will keep it here."
    used = sum(f["bytes"] for f in files)
    lines = [f"Your files ({_mb(used)} of {owner_files.QUOTA_BYTES // (1024 * 1024)} MB used):"]
    lines += [f"#{f['no']} {f['name']} ({_mb(f['bytes'])}, {_when(f['saved'])})" for f in files[:LIST_MAX]]
    if len(files) > LIST_MAX:
        lines.append(f"...and {len(files) - LIST_MAX} more.")
    lines.append("To delete one: 'delete <number>'.")
    return "\n".join(lines)


def _deleted_text(base: str, tenant_id: str, now: datetime) -> str:
    files = owner_files.deleted_files(base, tenant_id, now)
    if not files:
        return "No deleted files to restore."
    lines = ["Deleted files you can still restore:"]
    lines += [f"#{f['no']} {f['name']} ({f['days_left']} days left)" for f in files]
    lines.append("To bring one back: 'restore <number>'.")
    return "\n".join(lines)


def _ask_text(base: str, tenant_id: str, no: int, now: datetime) -> str:
    f = next((x for x in owner_files.live_files(base, tenant_id, now) if x["no"] == no), None)
    if f is None:
        return f"There is no file #{no} to delete. Send 'files' to see your files."
    return (f"Delete #{no} {f['name']} ({_mb(f['bytes'])})? You can restore it within 30 days. "
            f"To confirm, send: yes delete {no}")


def _delete_text(base: str, tenant_id: str, no: int, now: datetime) -> str:
    f = owner_files.delete(base, tenant_id, no, now)
    if f is None:
        return f"There is no file #{no} to delete. Send 'files' to see your files."
    return f"Deleted #{no} {f['name']}. To get it back within 30 days, send: restore {no}"


def _restore_text(base: str, tenant_id: str, no: int, now: datetime) -> str:
    try:
        f = owner_files.restore(base, tenant_id, no, now)
    except owner_files.StorageFull as e:
        return str(e)
    if f is None:
        return f"I cannot restore #{no}: it is not in your deleted files, or 30 days have passed."
    return f"Restored #{no} {f['name']}."


def _parse(cmd: str):
    """(what, number) for a file command, or None. Nothing is read from the app until a command matches."""
    if cmd in _LIST:
        return "list", None
    if cmd in _DELETED:
        return "deleted", None
    if m := _CONFIRM.fullmatch(cmd):
        return "delete", int(m.group(1))
    if m := _DELETE.fullmatch(cmd):
        return "ask", int(m.group(1))
    if m := _RESTORE.fullmatch(cmd):
        return "restore", int(m.group(1))
    return None


async def handle(app, tenant_id: str, text: str) -> str | None:
    """The reply to a file command, or None if the text is not one (the message then goes on as usual)."""
    parsed = _parse(" ".join(_PUNCT.sub(" ", (text or "").lower()).split()))   # "Yes, delete 2." is the same as "yes delete 2"
    if parsed is None:
        return None
    what, no = parsed
    base = app.state.settings.owner_files_dir
    now = datetime.now(timezone.utc)
    if what == "list":
        return await asyncio.to_thread(_list_text, base, tenant_id, now)
    if what == "deleted":
        return await asyncio.to_thread(_deleted_text, base, tenant_id, now)
    if what == "delete":
        return await asyncio.to_thread(_delete_text, base, tenant_id, no, now)
    if what == "ask":
        return await asyncio.to_thread(_ask_text, base, tenant_id, no, now)
    return await asyncio.to_thread(_restore_text, base, tenant_id, no, now)
