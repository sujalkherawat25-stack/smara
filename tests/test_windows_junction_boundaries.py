"""Actual Windows reparse-point checks, without changing symlink privileges.

These complement, not replace, the separate symbolic-link tests.
"""
import asyncio
import os
from pathlib import Path
import subprocess

import pytest

from smara.git_agent import GitWorkspaceManager
from smara.native_tools import NativeTools


pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows directory junctions")


def junction(link: Path, target: Path):
    def literal(path):
        return "'" + str(path).replace("'", "''") + "'"

    shell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    subprocess.run(
        [str(shell), "-NoProfile", "-NonInteractive", "-Command",
         "$ErrorActionPreference='Stop'; New-Item -ItemType Junction -Path "
         + literal(link) + " -Target " + literal(target) + " | Out-Null"],
        check=True, capture_output=True, timeout=15,
    )
    assert link.resolve() == target.resolve()


def test_native_memory_denies_outside_directory_junction(tmp_path):
    workspace = tmp_path / "project"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    note = outside / "native-memory.md"
    note.write_text("synthetic outside memory", encoding="utf-8")
    junction(workspace / ".smara", outside)
    with pytest.raises(ValueError, match="escapes"):
        asyncio.run(NativeTools(workspace).call("memory_read", {}))
    assert note.read_text(encoding="utf-8") == "synthetic outside memory"


def test_git_conflict_edit_denies_outside_directory_junction(tmp_path):
    workspace = tmp_path / "project"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    note = outside / "conflict.txt"
    original = "<<<<<<< HEAD\nleft\n=======\nright\n>>>>>>> branch\n"
    note.write_text(original, encoding="utf-8")
    junction(workspace / "linked", outside)
    ok, message = GitWorkspaceManager(workspace).resolve_conflict("linked/conflict.txt")
    assert not ok and "outside the workspace" in message
    assert note.read_text(encoding="utf-8") == original
