"""Atomic Multi-File Refactoring Engine with Snapshot Rollback Ledger."""
from __future__ import annotations

import ast
import difflib
import hashlib
import json
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class FileChange:
    """Represents a change to a single file."""
    path: Path
    original_content: str
    new_content: str
    original_hash: str
    new_hash: str
    diff: str
    syntax_valid: bool = True
    error_message: str | None = None


class SnapshotManager:
    """Manages pre-flight backup snapshots and zero-risk rollback ledgers."""

    def __init__(self, workspace_root: Path | None = None):
        self.workspace = (workspace_root or Path.cwd()).resolve()
        self.snapshot_dir = self.workspace / ".smara" / "snapshots"
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)

    def create_session_dir(self, session_id: str | None = None) -> Path:
        sid = session_id or f"refactor_{int(time.time())}_{os.urandom(3).hex()}"
        path = self.snapshot_dir / sid
        path.mkdir(parents=True, exist_ok=True)
        return path

    def save_backup(self, session_dir: Path, target_file: Path) -> Path:
        """Save a pre-modification backup of a target file."""
        rel_path = target_file.resolve().relative_to(self.workspace)
        backup_file = session_dir / rel_path
        backup_file.parent.mkdir(parents=True, exist_ok=True)
        if target_file.exists():
            shutil.copy2(target_file, backup_file)
        else:
            # Marker for newly created files
            (session_dir / f"{rel_path}.new_file").touch()
        return backup_file

    def restore_session(self, session_dir: Path) -> list[str]:
        """Restore all files from session snapshot directory."""
        restored: list[str] = []
        if not session_dir.exists():
            return restored

        for root, _, files in os.walk(session_dir):
            for file in files:
                backup_path = Path(root) / file
                rel_path = backup_path.relative_to(session_dir)

                if str(rel_path).endswith(".new_file"):
                    # Delete file that was newly created during the session
                    orig_rel = Path(str(rel_path).removesuffix(".new_file"))
                    target_file = self.workspace / orig_rel
                    if target_file.exists():
                        target_file.unlink()
                        restored.append(f"Deleted new file: {orig_rel}")
                    continue

                target_file = self.workspace / rel_path
                target_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(backup_path, target_file)
                restored.append(f"Restored: {rel_path}")

        return restored


class AtomicRefactorSession:
    """Atomic multi-file refactoring session with strict validation & rollback."""

    def __init__(self, workspace_root: Path | None = None, session_id: str | None = None):
        self.workspace = (workspace_root or Path.cwd()).resolve()
        self.snapshot_mgr = SnapshotManager(self.workspace)
        self.session_id = session_id or f"refactor_{int(time.time())}_{os.urandom(3).hex()}"
        self.session_dir = self.snapshot_mgr.create_session_dir(self.session_id)
        self.staged_changes: dict[Path, FileChange] = {}
        self.committed = False

    def stage_change(self, file_path: str | Path, new_content: str) -> FileChange:
        """Stage a modification to a file with unified diff and syntax verification."""
        p = Path(file_path)
        abs_path = (self.workspace / p).resolve() if not p.is_absolute() else p.resolve()

        # Read original
        orig_content = abs_path.read_text(encoding="utf-8", errors="replace") if abs_path.exists() else ""
        orig_hash = hashlib.sha256(orig_content.encode("utf-8")).hexdigest()
        new_hash = hashlib.sha256(new_content.encode("utf-8")).hexdigest()

        # Generate Unified Diff
        rel_str = str(abs_path.relative_to(self.workspace)) if abs_path.is_relative_to(self.workspace) else abs_path.name
        diff_lines = list(difflib.unified_diff(
            orig_content.splitlines(),
            new_content.splitlines(),
            fromfile=f"a/{rel_str}",
            tofile=f"b/{rel_str}",
            lineterm="",
        ))
        diff_text = "\n".join(diff_lines)

        # Syntax Validation
        syntax_valid = True
        err_msg = None
        ext = abs_path.suffix.lower()

        if ext == ".py":
            try:
                ast.parse(new_content, filename=str(abs_path))
            except SyntaxError as exc:
                syntax_valid = False
                err_msg = f"Python syntax error at line {exc.lineno}: {exc.msg}"
        elif ext == ".json":
            try:
                json.loads(new_content)
            except ValueError as exc:
                syntax_valid = False
                err_msg = f"Invalid JSON syntax: {exc}"

        # Save backup snapshot
        self.snapshot_mgr.save_backup(self.session_dir, abs_path)

        change = FileChange(
            path=abs_path,
            original_content=orig_content,
            new_content=new_content,
            original_hash=orig_hash,
            new_hash=new_hash,
            diff=diff_text,
            syntax_valid=syntax_valid,
            error_message=err_msg,
        )
        self.staged_changes[abs_path] = change
        return change

    def validate_all(self) -> tuple[bool, list[str]]:
        """Verify that all staged changes pass syntax validation."""
        errors: list[str] = []
        for change in self.staged_changes.values():
            if not change.syntax_valid:
                rel = change.path.relative_to(self.workspace) if change.path.is_relative_to(self.workspace) else change.path.name
                errors.append(f"{rel}: {change.error_message}")
        return len(errors) == 0, errors

    def commit(self, force: bool = False) -> tuple[bool, list[str]]:
        """Atomically apply all staged changes to the filesystem."""
        valid, errors = self.validate_all()
        if not valid and not force:
            return False, [f"Validation failed: {e}" for e in errors]

        applied: list[str] = []
        try:
            for change in self.staged_changes.values():
                change.path.parent.mkdir(parents=True, exist_ok=True)
                change.path.write_text(change.new_content, encoding="utf-8")
                rel = change.path.relative_to(self.workspace) if change.path.is_relative_to(self.workspace) else change.path.name
                applied.append(str(rel))
            self.committed = True
            return True, applied
        except Exception as exc:
            # Immediate atomic rollback on I/O failure
            self.rollback()
            return False, [f"Commit failed: {exc}. Rolled back all changes."]

    def rollback(self) -> list[str]:
        """Restore workspace to pre-session snapshot state."""
        restored = self.snapshot_mgr.restore_session(self.session_dir)
        self.committed = False
        return restored

    def summary(self) -> dict[str, Any]:
        """Return a structured summary of the refactor session."""
        files = []
        total_additions = 0
        total_deletions = 0

        for change in self.staged_changes.values():
            rel = str(change.path.relative_to(self.workspace)) if change.path.is_relative_to(self.workspace) else change.path.name
            adds = sum(1 for line in change.diff.splitlines() if line.startswith("+") and not line.startswith("+++"))
            dels = sum(1 for line in change.diff.splitlines() if line.startswith("-") and not line.startswith("---"))
            total_additions += adds
            total_deletions += dels

            files.append({
                "file": rel,
                "additions": adds,
                "deletions": dels,
                "diff": change.diff,
                "syntax_valid": change.syntax_valid,
                "error": change.error_message,
            })

        return {
            "session_id": self.session_id,
            "committed": self.committed,
            "files_changed_count": len(self.staged_changes),
            "total_additions": total_additions,
            "total_deletions": total_deletions,
            "files": files,
        }
