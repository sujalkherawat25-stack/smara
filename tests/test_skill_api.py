from __future__ import annotations

import asyncio

import pytest

from smara import api
from smara.models import SkillCreate, SkillTeachRequest, SkillTestRequest
from smara.store import TaskStore


def _manifest(owner: str) -> dict:
    return {
        "schema_version": "smara.skill.v1",
        "name": "api-inspect",
        "version": "1.0.0",
        "description": "Inspect an approved workspace.",
        "permissions": {"capabilities": ["local_file_read"]},
        "risk": "safe",
        "provider": "smara",
        "owner": owner,
        "stages": [{"id": "inspect", "tool": "local_file_read", "operation": "tree", "arguments": {}}],
        "outputs": [{"name": "result", "type": "object"}],
        "tests": [{"name": "empty"}],
    }


def test_skill_handlers_are_account_scoped_and_enforce_lifecycle(monkeypatch, tmp_path):
    store = TaskStore(str(tmp_path / "smara.db"))
    monkeypatch.setattr(api, "store", store)

    created = asyncio.run(api.create_skill(SkillCreate(manifest=_manifest("acct_1")), user="acct_1"))
    assert created["state"] == "draft"
    assert asyncio.run(api.list_skills(user="acct_2")) == {"skills": []}
    with pytest.raises(api.HTTPException) as error:
        asyncio.run(api.get_skill("api-inspect", "1.0.0", user="acct_2"))
    assert error.value.status_code == 404

    tested = asyncio.run(api.test_skill("api-inspect", "1.0.0", SkillTestRequest(passed=True, run_id="run-api"), user="acct_1"))
    assert tested["state"] == "tested"
    published = asyncio.run(api.publish_skill("api-inspect", "1.0.0", user="acct_1"))
    assert published["state"] == "published"
    deprecated = asyncio.run(api.deprecate_skill("api-inspect", "1.0.0", user="acct_1"))
    assert deprecated["state"] == "deprecated"


def test_teach_handler_stores_a_draft_without_cross_account_owner_input(monkeypatch, tmp_path):
    store = TaskStore(str(tmp_path / "smara.db"))
    monkeypatch.setattr(api, "store", store)
    body = SkillTeachRequest(
        name="api-edit",
        version="1.0.0",
        description="Inspect then edit.",
        workflow=[
            {"stage": "inspect", "capability": "local_file_read", "payload": {"path": "$input.root"}},
            {"stage": "edit", "capability": "local_file_write", "payload": {"path": "note.txt", "content": "ok"}},
        ],
        tests=[{"name": "disposable", "inputs": {"root": "workspace"}}],
    )
    result = asyncio.run(api.teach_skill(body, user="acct_7"))
    assert result["manifest"]["owner"] == "acct_7"
    assert result["manifest"]["risk"] == "confirm"
