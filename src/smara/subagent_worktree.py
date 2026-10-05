"""Smara Subagent Git Worktree Isolation Module.

Provides standalone Git snapshots for delegated subagents.
Each worker subagent executes inside its own branch and worktree directory
under `<repo_root>/.worktrees/subagent-<id>`, ensuring concurrent workers never
conflict on files or corrupt the parent working copy.

Clean worktrees are pruned automatically. Worktrees containing commits or modified
files are preserved and their diff/branch reported to the parent orchestrator.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import stat
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_GIT_TIMEOUT = 30
_WORKTREES_DIRNAME = ".worktrees"
_BRANCH_NAMESPACE = "smara-subagent"


def _run_git(args: list[str], cwd: str, timeout: int = _GIT_TIMEOUT, *, env=None, input=None) -> subprocess.CompletedProcess:
    environment = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, **(env or {})}
    trusted_root = Path(cwd).resolve()
    result = subprocess.run(
        ["git", "-c", "safe.directory=" + trusted_root.as_posix(),
         "-c", "safe.directory=" + (trusted_root / ".git").as_posix(),
         "-c", "core.autocrlf=false", "-c", "core.hooksPath=" + os.devnull, *args],
        cwd=cwd,
        capture_output=True,
        text=False,
        timeout=timeout,
        env=environment,
        input=input.encode("utf-8") if isinstance(input, str) else input,
    )
    # Binary pipes avoid Windows newline conversion corrupting unified patches.
    return subprocess.CompletedProcess(result.args, result.returncode,
        result.stdout.decode("utf-8", errors="replace"), result.stderr.decode("utf-8", errors="replace"))


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


def create_subagent_worktree(
    parent_cwd: Optional[str | Path],
    subagent_id: Optional[str] = None,
    base_revision: Optional[str] = None,
) -> Optional[Dict[str, str]]:
    """Create an isolated worktree for a subagent worker.

    Returns dict with path, branch, repo_root, and base_commit, or None if not git.
    """
    repo_root = resolve_git_root(parent_cwd)
    if not repo_root:
        return None

    short_id = subagent_id or uuid.uuid4().hex[:12]
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,96}", short_id):
        raise ValueError("Invalid isolated snapshot id")
    wt_name = f"subagent-{short_id}"
    branch = f"{_BRANCH_NAMESPACE}/{wt_name}"
    wt_path = Path(repo_root) / _WORKTREES_DIRNAME / wt_name

    try:
        if base_revision and not re.fullmatch(r"[a-f0-9]{40,64}", base_revision):
            raise ValueError("Snapshot base must be an immutable Git commit")
        rev = _run_git(["rev-parse", base_revision or "HEAD"], cwd=repo_root)
        if rev.returncode != 0:
            return None
        base_commit = rev.stdout.strip()

        wt_path.parent.mkdir(parents=True, exist_ok=True)
        # Linked worktrees point outside their workspace mount. A standalone
        # clone has its own metadata and objects, so Git works inside Docker
        # without mounting the parent's repository or credential directories.
        if wt_path.exists():
            return None
        res = _run_git(["clone", "--no-hardlinks", "--no-checkout", "--", repo_root, str(wt_path)], cwd=repo_root)
        if res.returncode != 0:
            logger.debug("Failed to create git worktree: %s", res.stderr)
            return None
        _run_git(["remote", "remove", "origin"], cwd=str(wt_path))
        excluded = wt_path / ".git" / "info" / "exclude"
        excluded.write_text(".smara/\n.worktrees/\n__pycache__/\n.pytest_cache/\n.pytest_tmp/\n", encoding="utf-8")
        checked = _run_git(["checkout", "-b", branch, base_commit], cwd=str(wt_path))
        if checked.returncode:
            return None

        return {
            "path": str(wt_path),
            "branch": branch,
            "repo_root": repo_root,
            "base_commit": base_commit,
            "kind": "standalone_snapshot",
        }
    except Exception as e:
        logger.debug("Error creating worktree: %s", e)
        return None


def inspect_subagent_worktree(worktree_info: Dict[str, str]) -> Dict[str, Any]:
    """Inspect changes made inside the subagent worktree."""
    wt_path = worktree_info.get("path", "")
    base_commit = worktree_info.get("base_commit", "HEAD")
    if not wt_path or not Path(wt_path).exists():
        return {"has_changes": True, "is_dirty": True, "commits_count": 0, "diff": "", "error": "snapshot_missing"}

    try:
        # Check dirty unstaged/uncommitted files
        status_res = _run_git(["status", "--porcelain"], cwd=wt_path)
        is_dirty = bool(status_res.stdout.strip())

        # Check commit count since base
        rev_res = _run_git(["rev-list", f"{base_commit}..HEAD", "--count"], cwd=wt_path)
        commits_count = int(rev_res.stdout.strip()) if rev_res.returncode == 0 and rev_res.stdout.strip().isdigit() else 0

        # Extract diff
        # A disposable index includes new files in the patch without staging
        # anything in either the caller's checkout or the worker's real index.
        with tempfile.TemporaryDirectory(prefix="smara-diff-") as directory:
            environment = {"GIT_INDEX_FILE": str(Path(directory) / "index")}
            loaded = _run_git(["read-tree", base_commit], cwd=wt_path, env=environment)
            staged = _run_git(["add", "-A", "--", "."], cwd=wt_path, env=environment)
            if loaded.returncode or staged.returncode:
                raise RuntimeError("Could not capture the complete worker patch")
            diff_res = _run_git(["diff", "--cached", "--binary", base_commit], cwd=wt_path, env=environment)
            diff_text = diff_res.stdout if diff_res.returncode == 0 else ""

        has_changes = is_dirty or (commits_count > 0)
        from .harness import workspace_revision
        return {
            "has_changes": has_changes,
            "is_dirty": is_dirty,
            "commits_count": commits_count,
            "diff": diff_text,
            "revision": workspace_revision(Path(wt_path)),
        }
    except Exception as e:
        logger.debug("Error inspecting worktree: %s", e)
        return {"has_changes": True, "is_dirty": True, "commits_count": 0, "diff": "", "error": str(e)}


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
        if worktree_info.get("kind") == "standalone_snapshot":
            target = Path(wt_path).resolve()
            expected = Path(repo_root).resolve() / _WORKTREES_DIRNAME
            if target.parent != expected or not target.name.startswith("subagent-") or Path(wt_path).is_symlink():
                return False
            # The only recursive removal is this verified disposable snapshot.
            def unlock(function, path, exc):
                candidate = Path(path).resolve()
                if candidate != target and target not in candidate.parents:
                    raise ValueError("Snapshot cleanup escaped its validated target")
                os.chmod(path, stat.S_IWRITE)
                function(path)
            shutil.rmtree(target, onerror=unlock)
            return True
        # Remove worktree
        _run_git(["worktree", "remove", "--force", str(wt_path)], cwd=repo_root)
        # Delete branch
        if branch:
            _run_git(["branch", "-D", branch], cwd=repo_root)
        return True
    except Exception as e:
        logger.debug("Failed cleaning up worktree: %s", e)
        return False
