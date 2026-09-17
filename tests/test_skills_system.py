import tempfile
from pathlib import Path
import sys

sys.path.insert(0, "src")
from smara.skills_system import SkillsRegistry, parse_yaml_frontmatter


def test_skills_system():
    with tempfile.TemporaryDirectory() as tmpdir:
        ws = Path(tmpdir)
        skills_dir = ws / ".smara" / "skills"
        my_skill = skills_dir / "git-workflow"
        my_skill.mkdir(parents=True)

        skill_md = my_skill / "SKILL.md"
        skill_md.write_text(
            """---
name: git-workflow
description: Automated git branch and commit manager
version: 1.2.0
tags: [git, automation, vcs]
---

# Git Workflow Instructions
Always pull before push.
""",
            encoding="utf-8"
        )

        refs_dir = my_skill / "references"
        refs_dir.mkdir()
        (refs_dir / "conventions.md").write_text("Conventional commits format: feat:, fix:", encoding="utf-8")

        registry = SkillsRegistry(workspace_dir=ws)

        # Tier 1 test
        skills = registry.list_skills()
        assert len(skills) == 1
        assert skills[0]["name"] == "git-workflow"
        assert skills[0]["version"] == "1.2.0"
        assert "git" in skills[0]["tags"]

        # Tag filter test
        assert len(registry.list_skills(tag_filter="vcs")) == 1
        assert len(registry.list_skills(tag_filter="nonexistent")) == 0

        # Tier 2 test (load instructions)
        view = registry.view_skill("git-workflow")
        assert view["status"] == "success"
        assert "Always pull before push" in view["instructions"]

        # Tier 3 test (load linked reference)
        ref_view = registry.view_skill("git-workflow", relative_path="references/conventions.md")
        assert ref_view["status"] == "success"
        assert "Conventional commits format" in ref_view["content"]

        # Path traversal guard test
        bad_view = registry.view_skill("git-workflow", relative_path="../../etc/passwd")
        assert bad_view["status"] == "error"

    print("All skills_system tests passed successfully!")


def test_skill_reference_rejects_sibling_prefix_escape(tmp_path: Path):
    skill_dir = tmp_path / ".smara" / "skills" / "foo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: foo\n---\nInstructions", encoding="utf-8")
    sibling = tmp_path / ".smara" / "skills" / "foo-secret"
    sibling.mkdir()
    (sibling / "secret.txt").write_text("should not be readable", encoding="utf-8")

    registry = SkillsRegistry(workspace_dir=tmp_path)
    result = registry.view_skill("foo", relative_path="../foo-secret/secret.txt")
    assert result["status"] == "error"


def test_skill_asset_rejects_escape(tmp_path: Path):
    registry = SkillsRegistry(workspace_dir=tmp_path)
    result = registry.create_skill(
        "safe",
        "safe skill",
        [],
        "instructions",
        assets={"../escape.txt": "must not be written"},
    )
    assert result["status"] == "error"
    assert not (tmp_path / ".smara" / "escape.txt").exists()


if __name__ == "__main__":
    test_skills_system()
