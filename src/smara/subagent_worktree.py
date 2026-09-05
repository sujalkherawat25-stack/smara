"""Smara Subagent Git Worktree Isolation Module.

Provides isolated git worktrees for delegated subagents (especially coder roles).
Each worker subagent executes inside its own branch and worktree directory
under `<repo_root>/.worktrees/subagent-<id>`, ensuring concurrent workers never
conflict on files or corrupt the parent working copy.

Clean worktrees are pruned automatically. Worktrees containing commits or modified
files are preserved and their diff/branch reported to the parent orchestrator.
"""
from __future__ import annotations

import logging
import os
import subprocess
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_GIT_TIMEOUT = 30
_WORKTREES_DIRNAME = ".worktrees"
_BRANCH_NAMESPACE = "smara-subagent"


def _run_git(args: list[str], cwd: str, timeout: int = _GIT_TIMEOUT) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def resolve_git_root(path: Optional[str | Path]) -> Optional[str]:
    """Return the git toplevel for path, or None if not a git repository."""
    if not path:
        return None
    try:
        candidate = Path(path).resolve()
        if candidate.is_file():
            candidate = candidate.parent
        if not candidate.is_dir():
            return None
        res = _run_git(["rev-parse", "--show-toplevel"], cwd=str(candidate))
        if res.returncode == 0:
            root = res.stdout.strip()
            return root or None
    except Exception as e:
        logger.debug("Failed resolving git root: %s", e)
    return None


def _ensure_gitignore_entry(repo_root: str) -> None:
    gitignore = Path(repo_root) / ".gitignore"
    entry = f"{_WORKTREES_DIRNAME}/"
    try:
        existing = gitignore.read_text(encoding="utf-8", errors="replace") if gitignore.exists() else ""
        if entry not in existing.splitlines():
            with open(gitignore, "a", encoding="utf-8") as f:
                if existing and not existing.endswith("\n"):
                    f.write("\n")
                f.write(f"{entry}\n")
    except Exception as e:
        logger.debug("Could not update .gitignore for worktrees: %s", e)


def create_subagent_worktree(
    parent_cwd: Optional[str | Path],
    subagent_id: Optional[str] = None,
) -> Optional[Dict[str, str]]:
    """Create an isolated worktree for a subagent worker.

    Returns dict with path, branch, repo_root, and base_commit, or None if not git.
    """
    repo_root = resolve_git_root(parent_cwd)
    if not repo_root:
        return None

    short_id = (subagent_id or uuid.uuid4().hex[:8]).replace("/", "-")
    wt_name = f"subagent-{short_id}"
    branch = f"{_BRANCH_NAMESPACE}/{wt_name}"
    wt_path = Path(repo_root) / _WORKTREES_DIRNAME / wt_name

    try:
        _ensure_gitignore_entry(repo_root)
        rev = _run_git(["rev-parse", "HEAD"], cwd=repo_root)
        if rev.returncode != 0:
            return None
        base_commit = rev.stdout.strip()

        wt_path.parent.mkdir(parents=True, exist_ok=True)
        res = _run_git(["worktree", "add", "-b", branch, str(wt_path), "HEAD"], cwd=repo_root)
        if res.returncode != 0:
            logger.debug("Failed to create git worktree: %s", res.stderr)
            return None

        return {
            "path": str(wt_path),
            "branch": branch,
            "repo_root": repo_root,
            "base_commit": base_commit,
        }
    except Exception as e:
        logger.debug("Error creating worktree: %s", e)
        return None


def inspect_subagent_worktree(worktree_info: Dict[str, str]) -> Dict[str, Any]:
    """Inspect changes made inside the subagent worktree."""
    wt_path = worktree_info.get("path", "")
    base_commit = worktree_info.get("base_commit", "HEAD")
    if not wt_path or not Path(wt_path).exists():
        return {"has_changes": False, "is_dirty": False, "commits_count": 0, "diff": ""}

    try:
        # Check dirty unstaged/uncommitted files
        status_res = _run_git(["status", "--porcelain"], cwd=wt_path)
        is_dirty = bool(status_res.stdout.strip())

        # Check commit count since base
        rev_res = _run_git(["rev-list", f"{base_commit}..HEAD", "--count"], cwd=wt_path)
        commits_count = int(rev_res.stdout.strip()) if rev_res.returncode == 0 and rev_res.stdout.strip().isdigit() else 0

        # Extract diff
        diff_res = _run_git(["diff", base_commit], cwd=wt_path)
        diff_text = diff_res.stdout.strip() if diff_res.returncode == 0 else ""

        has_changes = is_dirty or (commits_count > 0)
        return {
            "has_changes": has_changes,
            "is_dirty": is_dirty,
            "commits_count": commits_count,
            "diff": diff_text,
        }
    except Exception as e:
        logger.debug("Error inspecting worktree: %s", e)
        return {"has_changes": False, "is_dirty": False, "commits_count": 0, "diff": ""}


def cleanup_subagent_worktree(worktree_info: Dict[str, str], force: bool = False) -> bool:
    """Prune worktree if clean, or force prune. Returns True if removed."""
    wt_path = worktree_info.get("path", "")
    branch = worktree_info.get("branch", "")
    repo_root = worktree_info.get("repo_root", "")
    if not wt_path or not repo_root:
        return False

    inspection = inspect_subagent_worktree(worktree_info)
    if not force and inspection["has_changes"]:
        # Work was done; keep worktree and branch intact for review/merge
        return False

    try:
        # Remove worktree
        _run_git(["worktree", "remove", "--force", str(wt_path)], cwd=repo_root)
        # Delete branch
        if branch:
            _run_git(["branch", "-D", branch], cwd=repo_root)
        return True
    except Exception as e:
        logger.debug("Failed cleaning up worktree: %s", e)
        return False