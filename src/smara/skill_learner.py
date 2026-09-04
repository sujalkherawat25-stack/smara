"""Closed-Loop Autonomous Skill Learning Engine for Smara.

Allows Smara to extract, refine, and recall reusable procedural reasoning templates
(Skills) across sessions, establishing parity with and surpassing NousResearch Hermes Agent's L4 Memory.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

LOG = logging.getLogger("smara.skill_learner")


@dataclass
class LearnedSkill:
    name: str
    description: str
    triggers: list[str] = field(default_factory=list)
    instructions_md: str = ""
    success_count: int = 1
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LearnedSkill:
        return cls(**data)


class SkillLearnerEngine:
    """Manages persistent learned procedural skills in .smara/skills/."""

    def __init__(self, workspace_root: Optional[Path] = None):
        self.workspace = (workspace_root or Path.cwd()).resolve()
        self.skills_dir = self.workspace / ".smara" / "skills"
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        self._seed_default_skills()

    def _seed_default_skills(self) -> None:
        """Seeds default repository-level skills if none exist."""
        if list(self.skills_dir.glob("*.json")):
            return

        default_skills = [
            LearnedSkill(
                name="tauri_desktop_build",
                description="Procedure for building and packaging native Tauri desktop binaries on Windows.",
                triggers=["tauri", "desktop build", "package desktop", "release binary", "compile tauri"],
                instructions_md=(
                    "### Procedure for Compiling Smara Desktop\n"
                    "1. Always run `npm run build` inside `apps/desktop` first to bundle frontend React assets.\n"
                    "2. Run `cargo check` inside `apps/desktop/src-tauri` to verify Rust bridge integrity.\n"
                    "3. Run `npx tauri build --no-bundle` to produce the standalone release executable.\n"
                    "4. Terminate any running `Smara-Desktop` instances before overwriting binary files.\n"
                    "5. Deploy binary to `C:\\Users\\sujal\\Desktop\\Smara-Desktop.exe` and test launch with `Start-Process`."
                ),
            ),
            LearnedSkill(
                name="multilang_ast_blast_radius",
                description="Cross-referencing changed files with AST Code Property Graph across Python, TS, and Rust.",
                triggers=["blast radius", "code graph", "impact analysis", "dependencies", "ast audit"],
                instructions_md=(
                    "### Procedure for Blast Radius Audits\n"
                    "1. Instantiate `CodePropertyGraph(workspace_root)`.\n"
                    "2. Call `graph.index()` to parse `.py`, `.ts`, `.tsx`, `.rs`, and `.go` symbols.\n"
                    "3. Call `graph.blast_radius(target_file_or_symbol)`.\n"
                    "4. Inspect `impacted_files` and `callers` to assess downstream change risk before refactoring."
                ),
            ),
            LearnedSkill(
                name="pytest_self_healing",
                description="Autonomous test execution with traceback parsing and precision patch healing.",
                triggers=["pytest", "broken tests", "self healing", "test fixer", "heal test"],
                instructions_md=(
                    "### Procedure for Self-Healing Broken Tests\n"
                    "1. Use `PytestRunner(workspace_root)` with `--basetemp` to avoid file lock collisions.\n"
                    "2. Parse failing test names and exact assertion traceback line numbers.\n"
                    "3. Snapshot touched files using `RefactorTracker` before applying edits.\n"
                    "4. Apply minimal targeted repairs and rerun tests to confirm zero regressions."
                ),
            ),
        ]

        for s in default_skills:
            self.save_skill(s)

    def _file_path(self, name: str) -> Path:
        clean = re.sub(r"[^a-zA-Z0-9_-]", "_", name.strip().lower())
        return self.skills_dir / f"{clean}.json"

    def save_skill(self, skill: LearnedSkill) -> Path:
        p = self._file_path(skill.name)
        p.write_text(json.dumps(skill.to_dict(), indent=2), encoding="utf-8")
        return p

    def learn_skill(
        self,
        name: str,
        description: str,
        triggers: list[str],
        instructions_md: str,
    ) -> LearnedSkill:
        """Learns a new skill or refines an existing skill procedure."""
        existing = self.get_skill(name)
        now = time.time()
        if existing:
            existing.description = description or existing.description
            existing.triggers = list(set(existing.triggers + triggers))
            existing.instructions_md = instructions_md or existing.instructions_md
            existing.success_count += 1
            existing.updated_at = now
            self.save_skill(existing)
            return existing

        new_skill = LearnedSkill(
            name=name,
            description=description,
            triggers=triggers,
            instructions_md=instructions_md,
            success_count=1,
            created_at=now,
            updated_at=now,
        )
        self.save_skill(new_skill)
        return new_skill

    def get_skill(self, name: str) -> Optional[LearnedSkill]:
        p = self._file_path(name)
        if not p.exists():
            return None
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return LearnedSkill.from_dict(data)
        except Exception:
            return None

    def list_skills(self) -> list[LearnedSkill]:
        skills = []
        for p in self.skills_dir.glob("*.json"):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                skills.append(LearnedSkill.from_dict(data))
            except Exception:
                continue
        return sorted(skills, key=lambda s: s.success_count, reverse=True)

    def find_relevant_skills(self, query: str, top_k: int = 3) -> list[LearnedSkill]:
        """Finds skills matching query keywords or trigger phrases."""
        q_lower = query.lower()
        scored: list[tuple[int, LearnedSkill]] = []
        for s in self.list_skills():
            score = 0
            if s.name.lower() in q_lower:
                score += 10
            for t in s.triggers:
                if t.lower() in q_lower:
                    score += 5
            for word in s.description.lower().split():
                if len(word) > 3 and word in q_lower:
                    score += 1
            if score > 0:
                scored.append((score, s))

        scored.sort(key=lambda item: item[0], reverse=True)
        return [s for _, s in scored[:top_k]]
