"""Tests for Smara Subagent Git Worktree Isolation."""
import subprocess
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
    if not wt_info:
        pytest.skip("Worktree creation not supported or git lock active")

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
