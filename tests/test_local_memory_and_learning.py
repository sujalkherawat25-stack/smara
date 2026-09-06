from __future__ import annotations

import json
from pathlib import Path

from smara.local_conversation_memory import SQLiteConversationMemory
from smara.local_learning import LocalSkillLearningEngine
from smara.local_agent_runtime import run_shared_local_turn
from smara.skills_system import SkillsRegistry


def test_fts_memory_survives_reopen_and_is_workspace_scoped(tmp_path: Path):
    db = tmp_path / "conversation-memory.sqlite3"
    first = SQLiteConversationMemory(db)
    first.append_exchange(
        conversation_id="chat-one",
        workspace_id="workspace-a",
        user_message="Remember the blue deployment checklist.",
        assistant_message="Stored the blue deployment checklist.",
    )
    reopened = SQLiteConversationMemory(db)
    hits = reopened.search("blue deployment", workspace_id="workspace-a")
    assert len(hits) == 2
    assert any("checklist" in hit["content"].lower() for hit in hits)
    assert reopened.search("blue deployment", workspace_id="workspace-b") == []
    assert reopened.status()["fts5"] is True


def test_fts_sequence_is_scoped_by_workspace(tmp_path: Path):
    memory = SQLiteConversationMemory(tmp_path / "conversation-memory.sqlite3")
    memory.append_turn(conversation_id="same", workspace_id="one", role="user", content="workspace one")
    memory.append_turn(conversation_id="same", workspace_id="two", role="user", content="workspace two")
    assert [item["sequence"] for item in memory.recent(workspace_id="one")] == [0]
    assert [item["sequence"] for item in memory.recent(workspace_id="two")] == [0]


def test_skill_learning_writes_tested_playbook_from_completed_run(tmp_path: Path):
    engine = LocalSkillLearningEngine(tmp_path)
    engine.record_task(
        prompt="Inspect the workspace and calculate a release count.",
        conversation_id="chat-one",
        result={
            "completed": True,
            "answer": "Inspected and calculated the release count.",
            "steps": [
                {
                    "capability": "local_file_read",
                    "payload": {"operation": "list_tree", "path": "src"},
                },
                {
                    "capability": "local_calculate",
                    "payload": {"operation": "calculate", "expression": "2 + 2"},
                },
            ],
        },
    )
    learned = engine.learn_latest("/learn release-audit Check release evidence")
    assert learned["completed"] is True
    skill_path = tmp_path / ".smara" / "skills" / "release-audit" / "SKILL.md"
    assert skill_path.is_file()
    text = skill_path.read_text(encoding="utf-8")
    assert "tested: true" in text
    assert "local_file_read" in text and "local_calculate" in text
    manifest = json.loads((skill_path.parent / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["test"]["passed"] is True
    discovered = SkillsRegistry(workspace_dir=tmp_path).list_skills()
    assert any(item["name"] == "release-audit" for item in discovered)


def test_learn_command_is_local_and_does_not_need_a_model(tmp_path: Path):
    state = tmp_path / "desktop.json"
    state.write_text("{}", encoding="utf-8")
    engine = LocalSkillLearningEngine(tmp_path)
    engine.record_task(
        prompt="Read the README.",
        result={
            "completed": True,
            "answer": "README inspected.",
            "steps": [{"capability": "local_file_read", "payload": {"operation": "read_file", "path": "README.md"}}],
        },
    )
    result = run_shared_local_turn(
        prompt="/learn readme-review",
        state_path=state,
        workspace_id=str(tmp_path),
        config=type("Config", (), {"base_url": "http://127.0.0.1:1", "model": "unused", "api_key": "", "auth_header": "authorization", "label": "unused", "timeout_seconds": 1.0, "max_tokens": 512})(),
    )
    assert result["completed"] is True
    assert "readme-review" in result["answer"]
