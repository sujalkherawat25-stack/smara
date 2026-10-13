"""Explicit project files only; transfers never execute retrieved content."""
import hashlib
from pathlib import Path
import uuid

MAX_TRANSFER = 10 * 1024 * 1024
EXTENSIONS = {".pdf", ".txt", ".csv", ".json", ".png", ".jpg", ".jpeg", ".webp", ".docx", ".xlsx"}


def upload_file(workspace, name):
    root = Path(workspace).resolve(strict=True)
    path = Path(name)
    if not path.is_absolute(): path = root / path
    path = path.resolve(strict=True)
    if not path.is_relative_to(root) or not path.is_file(): raise ValueError("Choose an existing project file")
    parts = path.relative_to(root).parts
    if any(part.startswith(".") for part in parts) or path.suffix.lower() not in EXTENSIONS:
        raise ValueError("Hidden/credential/executable files cannot be uploaded")
    if not 0 < path.stat().st_size <= MAX_TRANSFER: raise ValueError("Transfer exceeds 10 MB")
    return path


def save_download(workspace, download):
    try:
        extension = Path(download.suggested_filename).suffix.lower()
        if extension not in EXTENSIONS: raise ValueError("Download type not permitted; never run retrieved files")
        source = Path(download.path())
        if not 0 < source.stat().st_size <= MAX_TRANSFER: raise ValueError("Download exceeds 10 MB")
        root = Path(workspace).resolve(strict=True)
        directory = root / ".smara/downloads"
        if not directory.resolve().is_relative_to(root): raise ValueError("Download directory escapes project")
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / (uuid.uuid4().hex + extension)
        data = source.read_bytes()
        if len(data) > MAX_TRANSFER: raise ValueError("Download grew beyond 10 MB")
        with target.open("xb") as output: output.write(data)
        return {"path": str(target), "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data), "executed": False}
    finally:
        download.delete()  # Delete only this owned Playwright temporary download.
