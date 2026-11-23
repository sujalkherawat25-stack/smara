"""The real install location of a program that may be seen through a Windows app package's private copy.

An app started from inside a packaged Windows app (for example the Claude desktop app's sandbox) sees %LOCALAPPDATA%
through a per-package folder: .../AppData/Local/Packages/<package>/LocalCache/Local/Smara Desktop/smara-desktop.exe.
A login-startup entry or scheduled task written from there points at that copy, which does not receive updates. This
maps such a path back to the real one when the real file exists, and leaves every other path alone.
"""
from __future__ import annotations

from pathlib import Path


def real_path(path: Path) -> Path:
    parts = path.parts
    lowered = [part.casefold() for part in parts]
    for index in range(len(parts) - 6):
        if lowered[index:index + 3] == ["appdata", "local", "packages"] and lowered[index + 4:index + 6] == ["localcache", "local"]:
            real = Path(*parts[:index + 2], *parts[index + 6:])
            return real if real.is_file() else path
    return path
