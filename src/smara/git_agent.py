"""Smart Git Workspace & Autonomous Branching Agent."""
from __future__ import annotations

import os
import re
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class GitFileChange:
    path: str
    status: str  # "modified" | "added" | "deleted" | "untracked" | "conflict"
    staged: bool = False


@dataclass
class GitCommitItem:
    commit_hash: str
    short_hash: str
    author: str
    date: str
    message: str


@dataclass
class GitStatusResult:
    is_repo: bool
    branch: str
    is_clean: bool
    staged_files: list[str]
    unstaged_files: list[str]
    untracked_files: list[str]
    conflicts: list[str]
    total_changes: int
    raw_diff: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class GitWorkspaceManager:
    """Manages git operations, status detection, branching, and commit generation."""

    def __init__(self, workspace_root: Path | None = None):
        self.workspace = (workspace_root or Path.cwd()).resolve()

    def _run_git(self, args: list[str]) -> tuple[int, str, str]:
        try:
            res = subprocess.run(
                ["git"] + args,
                cwd=str(self.workspace),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            return res.returncode, res.stdout.strip(), res.stderr.strip()
        except FileNotFoundError:
            return 127, "", "git executable not found in PATH"

    def is_git_repo(self) -> bool:
        code, _, _ = self._run_git(["rev-parse", "--is-inside-work-tree"])
        return code == 0

    def get_status(self) -> GitStatusResult:
        if not self.is_git_repo():
            return GitStatusResult(
                is_repo=False,
                branch="",
                is_clean=True,
                staged_files=[],
                unstaged_files=[],
                untracked_files=[],
                conflicts=[],
                total_changes=0,
            )

        # Get branch
        _, branch, _ = self._run_git(["rev-parse", "--abbrev-ref", "HEAD"])
        if not branch:
            branch = "main"

        # Get status porcelain
        _, out, _ = self._run_git(["status", "--porcelain=v1"])

        staged: list[str] = []
        unstaged: list[str] = []
        untracked: list[str] = []
        conflicts: list[str] = []

        for line in out.splitlines():
            if len(line) < 4:
                continue
            x = line[0]
            y = line[1]
            file_name = line[3:].strip()

            if x == "U" or y == "U" or (x == "A" and y == "A") or (x == "D" and y == "D"):
                conflicts.append(file_name)
            else:
                if x in {"M", "A", "D", "R"}:
                    staged.append(file_name)
                if y in {"M", "D"}:
                    unstaged.append(file_name)
                if x == "?" and y == "?":
                    untracked.append(file_name)

        # Get unified diff of unstaged + staged
        _, diff_unstaged, _ = self._run_git(["diff"])
        _, diff_staged, _ = self._run_git(["diff", "--cached"])
        combined_diff = (diff_staged + "\n" + diff_unstaged).strip()

        total = len(staged) + len(unstaged) + len(untracked) + len(conflicts)
        return GitStatusResult(
            is_repo=True,
            branch=branch,
            is_clean=total == 0,
            staged_files=staged,
            unstaged_files=unstaged,
            untracked_files=untracked,
            conflicts=conflicts,
            total_changes=total,
            raw_diff=combined_diff[:6000],  # preview cap
        )

    def list_branches(self) -> list[str]:
        if not self.is_git_repo():
            return []
        code, out, _ = self._run_git(["branch", "--list"])
        if code != 0:
            return []
        branches = []
        for line in out.splitlines():
            b = line.strip().removeprefix("* ").strip()
            if b:
                branches.append(b)
        return branches

    def create_branch(self, branch_name: str, checkout: bool = True) -> tuple[bool, str]:
        clean_name = re.sub(r"[^\w\-/]", "-", branch_name).strip("-")
        if not clean_name:
            return False, "Invalid branch name"

        if checkout:
            code, out, err = self._run_git(["checkout", "-b", clean_name])
        else:
            code, out, err = self._run_git(["branch", clean_name])

        if code == 0:
            return True, f"Created branch '{clean_name}'"
        return False, err or out or f"Failed to create branch '{clean_name}'"

    def switch_branch(self, branch_name: str) -> tuple[bool, str]:
        code, out, err = self._run_git(["checkout", branch_name.strip()])
        if code == 0:
            return True, f"Switched to branch '{branch_name}'"
        return False, err or out or f"Failed to switch to branch '{branch_name}'"

    def generate_smart_commit_message(self, diff_text: str | None = None) -> dict[str, str]:
        """Analyze changes and generate Conventional Commit title & description."""
        status = self.get_status()
        if status.is_clean:
            return {
                "title": "chore: working tree clean",
                "description": "No uncommitted modifications detected.",
                "type": "chore",
            }

        diff = diff_text if diff_text is not None else status.raw_diff

        # Heuristic detection of commit type
        all_files = status.staged_files + status.unstaged_files + status.untracked_files
        has_tests = any("test" in f.lower() for f in all_files)
        has_docs = any(f.endswith(".md") or "doc" in f.lower() for f in all_files)
        has_ui = any(f.endswith((".tsx", ".jsx", ".css", ".html")) for f in all_files)
        has_rust = any(f.endswith(".rs") for f in all_files)
        has_py = any(f.endswith(".py") for f in all_files)

        commit_type = "feat"
        if has_tests and not (has_ui or has_rust or has_py):
            commit_type = "test"
        elif has_docs and not (has_ui or has_rust or has_py or has_tests):
            commit_type = "docs"
        elif "fix" in diff.lower() or "error" in diff.lower() or "bug" in diff.lower():
            commit_type = "fix"
        elif "refactor" in diff.lower() or "clean" in diff.lower():
            commit_type = "refactor"

        # Scope detection
        scope = "core"
        if has_ui and has_rust:
            scope = "desktop"
        elif has_ui:
            scope = "ui"
        elif has_tests:
            scope = "tests"
        elif has_rust:
            scope = "backend"
        elif "cli" in " ".join(all_files):
            scope = "cli"

        # Formulate summary bullets
        bullets = []
        for f in all_files[:5]:
            bullets.append(f"- update {f}")
        if len(all_files) > 5:
            bullets.append(f"- and {len(all_files) - 5} additional files")

        title = f"{commit_type}({scope}): update {', '.join(all_files[:2])}"
        if len(all_files) > 2:
            title += f" and {len(all_files) - 2} more"

        description = "\n".join(bullets)
        return {
            "title": title[:72],
            "description": description,
            "type": commit_type,
            "scope": scope,
        }

    def commit(self, message: str, stage_all: bool = True) -> tuple[bool, str]:
        if not message.strip():
            return False, "Commit message cannot be empty"

        if stage_all:
            code_stage, _, err_stage = self._run_git(["add", "-A"])
            if code_stage != 0:
                return False, f"Failed to stage changes: {err_stage}"

        code, out, err = self._run_git(["commit", "-m", message.strip()])
        if code == 0:
            return True, out or "Committed successfully"
        return False, err or out or "Commit failed"

    def get_recent_commits(self, limit: int = 15) -> list[dict[str, Any]]:
        if not self.is_git_repo():
            return []
        fmt = "%H%x1f%h%x1f%an%x1f%ar%x1f%s"
        code, out, _ = self._run_git(["log", f"-n{limit}", f"--pretty=format:{fmt}"])
        if code != 0:
            return []

        commits = []
        for line in out.splitlines():
            parts = line.split("\x1f")
            if len(parts) == 5:
                commits.append({
                    "commit_hash": parts[0],
                    "short_hash": parts[1],
                    "author": parts[2],
                    "date": parts[3],
                    "message": parts[4],
                })
        return commits

    def detect_conflicts(self) -> list[dict[str, Any]]:
        """Find files with actual git merge conflict markers and extract conflicting sections."""
        conflicts = []
        for root, dirs, files in os.walk(self.workspace):
            # Prune ignored directories in place
            dirs[:] = [d for d in dirs if d not in {".git", "node_modules", "target", ".smara", "dist", "build"}]
            for file in files:
                p = Path(root) / file
                if p.suffix in {".py", ".ts", ".tsx", ".rs", ".js", ".json", ".md", ".css"}:
                    try:
                        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
                        has_start = any(l.startswith("<<<<<<< ") for l in lines)
                        has_sep = any(l.startswith("=======") for l in lines)
                        has_end = any(l.startswith(">>>>>>> ") for l in lines)
                        if has_start and has_sep and has_end:
                            rel = str(p.relative_to(self.workspace))
                            conflicts.append({
                                "file": rel,
                                "path": str(p),
                            })
                    except Exception:
                        pass
        return conflicts

    def resolve_conflict(self, file_path: str, strategy: str = "ours") -> tuple[bool, str]:
        """Resolve conflict markers by choosing 'ours', 'theirs', or clean merge."""
        p = self.workspace / file_path if not Path(file_path).is_absolute() else Path(file_path)
        if not p.exists():
            return False, f"File {file_path} not found"

        content = p.read_text(encoding="utf-8", errors="replace")
        if "<<<<<<< " not in content:
            return True, "No conflict markers found in file"

        # Regex to match <<<<<<< HEAD ... ======= ... >>>>>>> branch
        pattern = re.compile(r"<<<<<<< .*?\n(.*?)\n=======\n(.*?)\n>>>>>>> .*?\n", re.DOTALL)

        def replacer(match: re.Match) -> str:
            ours = match.group(1)
            theirs = match.group(2)
            if strategy == "ours":
                return ours + "\n"
            elif strategy == "theirs":
                return theirs + "\n"
            else:  # union / smart merge
                return ours + "\n" + theirs + "\n"

        resolved = pattern.sub(replacer, content)
        p.write_text(resolved, encoding="utf-8")
        return True, f"Resolved conflicts in {file_path} using strategy '{strategy}'"

    def get_file_diff(self, file_path: str) -> dict[str, Any]:
        """Returns structured unified diff lines for a specific file."""
        clean_path = file_path.replace("\\", "/").strip()
        code, out, _ = self._run_git(["diff", "HEAD", "--", clean_path])
        if code != 0 or not out:
            p = self.workspace / clean_path
            if p.exists() and p.is_file():
                try:
                    content = p.read_text(encoding="utf-8", errors="replace")
                    lines = [{"type": "add", "text": line, "line_no": i + 1} for i, line in enumerate(content.splitlines()[:500])]
                    return {
                        "file": clean_path,
                        "raw_diff": "",
                        "lines": lines,
                        "additions": len(lines),
                        "deletions": 0,
                        "is_untracked": True,
                    }
                except Exception:
                    pass
            return {"file": clean_path, "raw_diff": "", "lines": [], "additions": 0, "deletions": 0, "is_untracked": False}

        lines = []
        adds = 0
        dels = 0
        for line in out.splitlines():
            if line.startswith("+++ ") or line.startswith("--- ") or line.startswith("diff --git ") or line.startswith("index "):
                continue
            if line.startswith("@@"):
                lines.append({"type": "hunk", "text": line})
            elif line.startswith("+"):
                adds += 1
                lines.append({"type": "add", "text": line[1:]})
            elif line.startswith("-"):
                dels += 1
                lines.append({"type": "del", "text": line[1:]})
            else:
                lines.append({"type": "context", "text": line[1:] if line.startswith(" ") else line})

        return {
            "file": clean_path,
            "raw_diff": out[:50_000],
            "lines": lines[:600],
            "additions": adds,
            "deletions": dels,
            "is_untracked": False,
        }
