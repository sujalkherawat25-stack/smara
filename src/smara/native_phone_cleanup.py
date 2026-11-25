"""After a phone task: remove what Smara made only to read the owner's documents.

Only two kinds of file are ever removed, both inside the phone tasks folder and both created during the task:
pictures under pdf-previews/ (page pictures and any crops of them), and a copy of a file (in the folder or any folder
inside it) whose identical original is still on the PC. Anything else the task wrote, anything that existed before, and a copy with no matching original stay
where they are.
"""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

KEEP_WORDS = re.compile(r"\b(keep|copy|copies|move|save)\b", re.I)
NEGATED = re.compile(r"\b(not|don't|dont|never|without|no|nothing)\b", re.I)
SEARCH_FOLDERS = ("Downloads", "Desktop", "Documents")
MAX_LOOKED_AT = 20_000
SLACK_S = 2
PICTURES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
MENTIONS_A_COPY = re.compile(r"\b(cop(?:y|ies|ied)|saved)\b", re.I)


def asked_to_keep(task: str) -> bool:
    """The owner's own words ask for a copy or a saved file. A sentence that forbids it ("do not copy") does not count."""
    return any(KEEP_WORDS.search(part) and not NEGATED.search(part) for part in re.split(r"[.!?\n;]+", task))


def _created(info: os.stat_result) -> float:
    return getattr(info, "st_birthtime", info.st_ctime)


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def has_original(copy: Path, folder: Path, home: Path) -> bool:
    """An identical file outside the phone tasks folder (Downloads, Desktop or Documents, a few levels deep)."""
    size, wanted, looked = copy.stat().st_size, None, 0
    for name in SEARCH_FOLDERS:
        base = home / name
        for current, folders, files in os.walk(base):
            here = Path(current)
            deep = len(here.relative_to(base).parts) >= 3
            folders[:] = [] if deep else [f for f in folders if not f.startswith(".") and (here / f).resolve() != folder]
            for file in files:
                looked += 1
                if looked > MAX_LOOKED_AT:
                    return False
                other = here / file
                try:
                    if other.stat().st_size != size:
                        continue
                    wanted = wanted or _digest(copy)
                    if _digest(other) == wanted and not other.samefile(copy):
                        return True
                except OSError:
                    continue
    return False


def drop_stale_mentions(text: str, removed: list[str]) -> str:
    """Take out sentences that still say a removed copy or picture is saved on the PC."""
    names = {Path(r).name.lower() for r in removed if not r.startswith("pdf-previews/")}
    if not removed:
        return text

    def stale(sentence: str) -> bool:
        low = sentence.lower()
        if "pdf-previews" in low:
            return True
        return bool(MENTIONS_A_COPY.search(low)) and "original" not in low and any(name in low for name in names)

    kept = []
    for line in text.split("\n"):
        parts = re.split(r"(?<=[.!?])\s+", line)
        left = [part for part in parts if not stale(part)]
        if left or len(parts) == 0:
            kept.append(" ".join(left) if len(left) != len(parts) else line)
    out = re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()
    return out or text


def clean_up(workspace: str, since: float, task: str, home: Path | None = None) -> list[str]:
    """Remove the temporary files of a finished task; returns their paths relative to the phone tasks folder."""
    if not workspace or not since or asked_to_keep(task):
        return []
    try:
        folder = Path(workspace).resolve(strict=True)
    except OSError:
        return []
    home = home or Path.home()
    removed: list[str] = []

    def remove(path: Path) -> None:
        try:
            path.unlink()
            removed.append(path.relative_to(folder).as_posix())
        except OSError:
            pass

    previews = folder / "pdf-previews"
    if previews.is_dir() and previews.resolve() == previews:             # never follow a link out of the folder
        for picture in sorted(previews.rglob("*")):
            if (picture.suffix.lower() in PICTURES and picture.is_file() and not picture.is_symlink()
                    and picture.resolve().is_relative_to(previews) and _created(picture.stat()) >= since - SLACK_S):
                remove(picture)
        for empty in sorted((p for p in previews.rglob("*") if p.is_dir()), reverse=True) + [previews]:
            try:
                empty.rmdir()
            except OSError:
                pass
    emptied: set[Path] = set()
    for file in _files_inside(folder):
        info = file.stat()
        # Made here during the task, but its content is older than the task: a copy that kept the original's date.
        if (_created(info) >= since - SLACK_S and info.st_mtime < since - SLACK_S and info.st_size
                and has_original(file, folder, home)):
            before = len(removed)
            remove(file)
            if len(removed) > before:
                emptied.add(file.parent)
    # A folder the task made only to hold such copies goes too, once it is empty.
    for holder in sorted(emptied, key=lambda p: len(p.parts), reverse=True):
        while holder != folder and holder.is_relative_to(folder):
            try:
                if _created(holder.stat()) < since - SLACK_S:
                    break
                holder.rmdir()
            except OSError:
                break
            holder = holder.parent
    return removed


def _files_inside(folder: Path):
    """Ordinary files in the folder and below, not following links, not Smara's own state or the page pictures."""
    seen = 0
    for current, folders, files in os.walk(folder):
        here = Path(current)
        folders[:] = sorted(f for f in folders if f.lower() not in {".smara", ".git", "pdf-previews"}
                            and not (here / f).is_symlink() and (here / f).resolve() == here / f)
        for name in sorted(files):
            seen += 1
            if seen > MAX_LOOKED_AT:
                return
            if not (here / name).is_symlink():
                yield here / name
