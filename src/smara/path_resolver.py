"""Autonomous Path & Folder Discovery Engine for Smara.

Locates folders and files across the user's system (workspaces, home directory,
parent trees, documents) by name without requiring absolute paths, and provides
unrestricted whole-file read access.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any, Optional


MAX_WHOLE_FILE_BYTES = 32 * 1024 * 1024  # 32 MB full file reading capacity


def clean_resource_name(raw: str) -> str:
    """Clean natural language folder or file references (e.g. 'memoryos folder' -> 'memoryos')."""
    s = raw.strip().strip("'\"`")
    # Remove leading/trailing conversational filler
    for prefix in ["folder ", "directory ", "file ", "repo ", "project "]:
        if s.lower().startswith(prefix):
            s = s[len(prefix):].strip()
    for suffix in [" folder", " directory", " repo", " project"]:
        if s.lower().endswith(suffix):
            s = s[:-len(suffix)].strip()
    return s.strip().strip("'\"`")


def locate_resource(name: str, base_roots: list[Path] | None = None) -> Optional[Path]:
    """Autonomously discover a folder or file anywhere across the user's system.
    
    Search precedence:
    1. Exact path if absolute and exists
    2. Direct path relative to provided base_roots
    3. Sibling workspaces (parent and grandparent of base_roots)
    4. User home root: Path.home() / name (e.g. C:\\Users\\sujal\\memoryos)
    5. Standard locations: Documents, OneDrive, Desktop
    6. Directory scan in Path.home() matching name case-insensitively
    """
    clean = clean_resource_name(name)
    if not clean:
        return None

    candidate = Path(clean).expanduser()
    if candidate.is_absolute() and candidate.exists():
        return candidate.resolve()

    roots = [r.resolve() for r in (base_roots or [Path.cwd()])]
    
    # 1. Under base roots
    for root in roots:
        target = (root / clean).resolve()
        if target.exists():
            return target

    # 2. Parent & grandparent directories (e.g. sibling folders)
    for root in roots:
        for parent_level in [root.parent, root.parent.parent]:
            if parent_level.exists():
                sibling = (parent_level / clean).resolve()
                if sibling.exists():
                    return sibling

    # 3. User home directory (e.g. C:\Users\sujal\memoryos)
    home = Path.home().resolve()
    direct_home = (home / clean).resolve()
    if direct_home.exists():
        return direct_home

    # 4. Common standard user subfolders
    for sub in ["Documents", "OneDrive\\Documents", "OneDrive", "Desktop", "Projects", "workspace", "code"]:
        cand = (home / sub / clean).resolve()
        if cand.exists():
            return cand

    # 5. Case-insensitive scan in home directory (depth 1-2)
    clean_lower = clean.lower()
    try:
        # First check immediate subdirectories of home
        for entry in home.iterdir():
            if entry.name.lower() == clean_lower:
                return entry.resolve()
            # Also check if entry matches with common separators
            if entry.name.lower().replace("-", "_") == clean_lower.replace("-", "_"):
                return entry.resolve()
    except Exception:
        pass

    # 6. Check common drives on Windows (e.g. C:\, D:\)
    for drive in ["C:\\", "D:\\"]:
        p = Path(drive) / clean
        if p.exists():
            return p.resolve()

    return None


def read_whole_file(file_path: Path, max_bytes: int = MAX_WHOLE_FILE_BYTES) -> dict[str, Any]:
    """Read a whole file with zero truncation up to max_bytes (default 32 MB)."""
    p = Path(file_path).resolve()
    if not p.is_file():
        raise FileNotFoundError(f"File not found: {p}")

    size = p.stat().st_size
    if size > max_bytes:
        raise ValueError(f"File size ({size} bytes) exceeds maximum whole-file limit ({max_bytes} bytes)")

    content_bytes = p.read_bytes()
    is_binary = p.suffix.lower() in {".pdf", ".docx", ".xlsx", ".pptx", ".zip", ".tar", ".gz", ".png", ".jpg", ".jpeg", ".ico", ".exe", ".bin"}
    if is_binary:
        return {
            "action": "local_file_read",
            "file_name": p.name,
            "path": str(p),
            "bytes_read": len(content_bytes),
            "total_lines": 0,
            "is_binary": True,
            "sha256": hashlib.sha256(content_bytes).hexdigest(),
            "content": f"[{p.suffix.upper().strip('.')} Document: {p.name} ({len(content_bytes):,} bytes)]",
            "content_shared": True,
        }

    text_content = content_bytes.decode("utf-8", errors="replace")
    lines = text_content.splitlines()

    return {
        "action": "local_file_read",
        "file_name": p.name,
        "path": str(p),
        "bytes_read": len(content_bytes),
        "total_lines": len(lines),
        "is_binary": False,
        "sha256": hashlib.sha256(content_bytes).hexdigest(),
        "content": text_content,
        "content_shared": True,
    }


def inspect_discovered_folder(folder_path: Path) -> dict[str, Any]:
    """Inspect and summarize a discovered folder, including reading README and key configs in full."""
    p = Path(folder_path).resolve()
    if not p.is_dir():
        raise NotADirectoryError(f"Directory not found: {p}")

    entries: list[dict[str, Any]] = []
    readme_content: Optional[str] = None
    readme_path: Optional[str] = None
    key_configs: dict[str, str] = {}

    try:
        for item in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            # Skip heavy noise dirs
            if item.name in {".git", "node_modules", ".venv", "__pycache__", "target"}:
                entries.append({"name": item.name, "type": "directory", "size": 0, "skipped": True})
                continue

            if item.is_dir():
                entries.append({"name": item.name, "type": "directory"})
            else:
                sz = item.stat().st_size
                entries.append({"name": item.name, "type": "file", "size": sz})

                # If this is README, read in full
                if item.name.lower().startswith("readme") and readme_content is None:
                    try:
                        f_info = read_whole_file(item)
                        readme_content = f_info["content"]
                        readme_path = str(item)
                    except Exception:
                        pass

                # If this is a key config (pyproject.toml, package.json, Cargo.toml), read in full
                if item.name in {"pyproject.toml", "package.json", "Cargo.toml", "docker-compose.yml"}:
                    try:
                        f_info = read_whole_file(item)
                        key_configs[item.name] = f_info["content"]
                    except Exception:
                        pass
    except Exception as exc:
        entries.append({"error": str(exc)})

    return {
        "folder_name": p.name,
        "absolute_path": str(p),
        "total_items": len(entries),
        "items": entries[:60],
        "readme_path": readme_path,
        "readme_content": readme_content,
        "key_configs": key_configs,
    }
