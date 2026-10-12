"""Tests for Smara Subagent Git Worktree Isolation."""
import subprocess
import os
import uuid
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


def test_worktree_lifecycle(tmp_path):
    # A tiny real Git repository exercises clone/inspect/cleanup without
    # cloning the multi-gigabyte application and vendored runtime per test.
    root = tmp_path / "fixture-repo"
    root.mkdir()
    environment = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}
    subprocess.run(["git", "init", "--quiet"], cwd=root, env=environment, check=True)
    (root / "README.md").write_text("isolated snapshot fixture\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=root, env=environment, check=True)
    subprocess.run(["git", "-c", "user.name=Smara Tests", "-c", "user.email=tests@example.invalid",
                    "-c", f"core.hooksPath={os.devnull}", "commit", "--quiet", "-m", "fixture"],
                   cwd=root, env=environment, check=True)

    wt_info = create_subagent_worktree(root, subagent_id=f"test_unit_{uuid.uuid4().hex}")
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
