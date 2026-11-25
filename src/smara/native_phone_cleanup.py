"""After a phone task: remove what Smara made only to read the owner's documents.

Only two kinds of file are ever removed, both inside the phone tasks folder and both created during the task:
page pictures under pdf-previews/, and a copy of a file whose identical original is still on the PC. Anything the task
wrote itself, anything that existed before, and a copy with no matching original stay where they are.
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
        for picture in sorted(previews.rglob("page-*.png")):
            if not picture.is_symlink() and picture.resolve().is_relative_to(previews) and _created(picture.stat()) >= since - SLACK_S:
                remove(picture)
        for empty in sorted((p for p in previews.rglob("*") if p.is_dir()), reverse=True) + [previews]:
            try:
                empty.rmdir()
            except OSError:
                pass
    for file in sorted(folder.iterdir()):
        if file.is_symlink() or not file.is_file():
            continue
        info = file.stat()
        # Made here during the task, but its content is older than the task: a copy that kept the original's date.
        if _created(info) >= since - SLACK_S and info.st_mtime < since - SLACK_S and has_original(file, folder, home):
            remove(file)
    return removed
