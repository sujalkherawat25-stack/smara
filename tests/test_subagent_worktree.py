"""Tests for Smara Subagent Git Worktree Isolation."""
import subprocess
import os
import tempfile
from pathlib import Path
import pytest
from smara.subagent_worktree import (
    resolve_git_root,
    create_subagent_worktree,
    inspect_subagent_worktree,
    cleanup_subagent_worktree,
)


def test_resolve_git_root():
    root = resolve_git_root(Path.cwd())
    assert root is not None
    assert Path(root).exists()


def test_worktree_lifecycle():
    # Use real workspace repo for a fast isolated test
    root = resolve_git_root(Path.cwd())
    if not root:
        pytest.skip("Not in a git repository")

    wt_info = create_subagent_worktree(root, subagent_id="test_unit_99")
    assert wt_info, "Isolated snapshot creation failed for the current Git repository"

    wt_path = Path(wt_info["path"])
    try:
        assert wt_path.exists()
        assert wt_info["branch"].startswith("smara-subagent/")

        # Initially clean
        insp = inspect_subagent_worktree(wt_info)
        assert not insp["has_changes"]

        # Clean prune should succeed
        cleaned = cleanup_subagent_worktree(wt_info)
        assert cleaned
        assert not wt_path.exists()
    finally:
        if wt_path.exists():
            cleanup_subagent_worktree(wt_info, force=True)


def test_git_trust_is_scoped_to_workspace_and_metadata(tmp_path, monkeypatch):
    import smara.subagent_worktree as module
    captured = {}
    def run(argv, **kwargs):
        captured.update(argv=argv, **kwargs)
        return subprocess.CompletedProcess(argv, 0, b"", b"")
    monkeypatch.setattr(module.subprocess, "run", run)
    module._run_git(["status"], str(tmp_path))
    trust = [item for item in captured["argv"] if item.startswith("safe.directory=")]
    assert trust == [
        "safe.directory=" + tmp_path.resolve().as_posix(),
        "safe.directory=" + (tmp_path.resolve() / ".git").as_posix(),
    ]
    assert captured["env"]["GIT_CONFIG_GLOBAL"] == os.devnull
    assert all("*" not in value for value in trust)
