import asyncio
import json
from dataclasses import replace

from fastapi import Response

from smara import admin
from smara.auth_store import AccountStore
from smara.store import TaskStore


def _bound(tmp_path):
    tasks = TaskStore(str(tmp_path / "smara.db"))
    accounts = AccountStore(database_path=str(tmp_path / "smara.db"))
    accounts.ensure_schema()
    admin.configure(tasks, accounts)
    return tasks, accounts


def test_operator_snapshot_keeps_smara_data_bounded(tmp_path):
    tasks, accounts = _bound(tmp_path)
    account = accounts.upsert_google_account(
        google_sub="operator-test", email="owner@example.com", display_name="Owner", avatar_url=None
    )
    task = tasks.create(account["id"], "default", "Build report", "private objective", False)
    claimed = tasks.claim_one("test-worker")
    assert claimed is not None
    tasks.complete_step(claimed["step_id"], account["id"], "private result")

    snapshot = admin._control_snapshot()
    assert snapshot["boundary"] == "Smara control plane only"
    assert snapshot["tasks"]["total"] == 1
    assert snapshot["tasks"]["with_results"] == 1
    assert snapshot["recent_tasks"][0]["task_id"] == task["id"]
    # The operator list exposes lifecycle metadata, never the objective or
    # final answer itself.
    encoded = json.dumps(snapshot)
    assert "private objective" not in encoded
    assert "private result" not in encoded


def test_operator_cookie_is_separate_from_account_session(monkeypatch):
    settings = replace(
        admin.settings,
        operator_secret="a" * 32,
        operator_cookie_name="test_operator",
        operator_session_hours=1,
        dev_mode=True,
    )
    monkeypatch.setattr(admin, "settings", settings)
    response = Response()
    asyncio.run(admin.operator_sign_in({"secret": settings.operator_secret}, response))
    cookie = response.headers["set-cookie"].split(";", 1)[0].split("=", 1)[1]
    assert admin.require_operator(cookie) == "operator"


def test_syntarus_probe_fails_closed_without_key(monkeypatch):
    settings = replace(admin.settings, syntarus_api_key="", syntarus_health_url="https://example.invalid/health")
    monkeypatch.setattr(admin, "settings", settings)
    result = asyncio.run(admin._syntarus_snapshot())
    assert result["status"] == "unconfigured"
    assert result["ok"] is False
    assert result["raw_memory_exposed"] is False


def test_operator_routes_are_mounted_on_native_smara_api():
    # OpenAPI is the stable public contract here. This guards against a
    # deployment accidentally importing the dashboard module without
    # registering its routes on the native Smara app.
    from smara.api import app

    paths = app.openapi()["paths"]
    assert "/v1/admin/session" in paths
    assert "/v1/admin/overview" in paths
    assert "/v1/admin/syntarus" in paths
    assert "/v1/executors/steps/{step_id}" in paths
