"""Telegram transport fixtures verify receipts/authorization, not live bot delivery."""
from concurrent.futures import ThreadPoolExecutor
import json
import time
import httpx
import pytest

from smara.native_goals import GoalStore
from smara.native_telegram import TelegramConnector, TelegramError, pieces


def setup(tmp_path, handler=None):
    store = GoalStore(tmp_path / "goals.sqlite")
    http = httpx.Client(transport=httpx.MockTransport(handler or (lambda request: httpx.Response(200, json={"ok": True, "result": {"message_id": 1}}))))
    connector = TelegramConnector(store, "synthetic-token-not-a-secret", http)
    with store.connect() as db:
        store.set_meta(db, "telegram_options", {"workspace": str(tmp_path), "profile_id": "synthetic", "sandbox": "read-only"})
    return store, connector


def update(identity, text, user=42, chat=42, kind="private", age=0):
    return {"update_id": identity, "message": {"date": int(time.time()) - age, "from": {"id": user}, "chat": {"id": chat, "type": kind}, "text": text}}


def pair(store, connector):
    code = store.pairing_code()
    connector.handle(update(1, "/link " + code))


def test_only_paired_private_identity_can_start_or_control_goals(tmp_path):
    store, connector = setup(tmp_path)
    pair(store, connector)
    connector.handle(update(2, "/goal forbidden", user=13))
    connector.handle(update(3, "/goal forbidden", chat=13))
    connector.handle(update(4, "/goal forbidden", kind="group"))
    assert store.list() == []
    connector.handle(update(5, "/goal Inspect fixture | Evidence required"))
    assert store.list()[0]["spec"]["objective"] == "Inspect fixture"
    assert store.list()[0]["spec"]["sandbox"] == "read-only"


def test_telegram_can_select_outcome_gates_without_changing_permissions(tmp_path):
    store, connector = setup(tmp_path)
    pair(store, connector)
    connector.handle(update(2, "/goal mixed: Inspect fixture | Tests and sources"))
    spec = store.list()[0]["spec"]
    assert spec["task_kind"] == "mixed" and spec["objective"] == "Inspect fixture"
    assert spec["sandbox"] == "read-only"


def test_concurrent_duplicate_update_creates_one_goal_and_survives_restart(tmp_path):
    store, connector = setup(tmp_path)
    pair(store, connector)
    message = update(2, "/goal Inspect fixture")
    with ThreadPoolExecutor(max_workers=5) as pool:
        list(pool.map(lambda _: connector.handle(message), range(5)))
    assert len(store.list()) == 1
    restarted = TelegramConnector(GoalStore(store.path), "synthetic-token-not-a-secret", connector.http)
    restarted.handle(message)
    assert len(store.list()) == 1
    with store.connect() as db:
        assert store.meta(db, "telegram_offset:" + connector.namespace) == 3


def test_pairing_code_is_single_use_and_expiring(tmp_path):
    store, connector = setup(tmp_path)
    code = store.pairing_code()
    connector.handle(update(1, "/link " + code))
    connector.handle(update(2, "/link " + code, user=13, chat=13))
    with store.connect() as db:
        assert store.meta(db, "telegram_binding")["user_id"] == 42
        store.set_meta(db, "pairing", {"digest": "unmatchable", "expires": 0})
    connector.handle(update(3, "/link expired", user=13, chat=13))
    assert len(store.list()) == 0


def test_old_start_command_is_not_executed_after_laptop_returns(tmp_path):
    store, connector = setup(tmp_path)
    pair(store, connector)
    connector.handle(update(2, "/goal Stale request", age=3600))
    assert store.list() == []
    connector.handle(update(3, "/goal Fresh request"))
    assert len(store.list()) == 1


def test_feedback_and_pause_target_shared_goal(tmp_path):
    store, connector = setup(tmp_path)
    pair(store, connector)
    connector.handle(update(2, "/goal Inspect fixture"))
    goal = store.list()[0]
    connector.handle(update(3, "Also inspect tests"))
    assert store.feedback(goal["id"])[0]["text"] == "Also inspect tests"
    connector.handle(update(4, "/pause " + goal["id"][:8]))
    assert GoalStore(store.path).get(goal["id"])["state"] == "paused"


def test_uncertain_send_is_not_replayed(tmp_path):
    observed = []
    def handler(request):
        observed.append(request.url.path)
        raise httpx.ReadTimeout("synthetic timeout", request=request)
    store, connector = setup(tmp_path, handler)
    pair(store, connector)
    assert connector.flush()
    assert not connector.flush()
    assert len(observed) == 1
    with store.connect() as db:
        assert db.execute("SELECT state FROM telegram_outbox").fetchone()[0] == "unknown"


def test_transport_error_does_not_expose_token(tmp_path):
    store, connector = setup(tmp_path, lambda request: httpx.Response(401, json={"description": "sensitive diagnostic"}))
    with pytest.raises(TelegramError) as caught:
        connector.api("getUpdates", {})
    assert "synthetic-token" not in str(caught.value)
    assert "sensitive" not in str(caught.value)


def test_emoji_chunks_use_utf16_limit():
    text = "😀" * 5000
    split = pieces(text)
    assert "".join(split) == text
    assert all(len(part.encode("utf-16-le")) // 2 <= 4000 for part in split)


def test_telegram_cannot_accept_results_or_grant_approvals(tmp_path):
    store, connector = setup(tmp_path)
    pair(store, connector)
    connector.handle(update(2, "/goal Inspect fixture"))
    goal = store.list()[0]
    connector.handle(update(3, "/accept " + goal["id"]))
    connector.handle(update(4, "/approve all"))
    assert store.get(goal["id"])["state"] == "queued"


def test_requested_deliverable_sends_only_a_recorded_project_output(tmp_path):
    observed = []
    def handler(request):
        observed.append((request.url.path, request.content))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
    store, connector = setup(tmp_path, handler)
    pair(store, connector)
    connector.handle(update(2, "/goal Deliver synthetic report"))
    identity = store.list()[0]["id"]
    (tmp_path / "report.md").write_text("Synthetic public-fixture report", encoding="utf-8")
    store.finish(store.claim(), {"decision": "ready_for_review", "outputs": ["report.md"], "evidence": ["Synthetic fixture"]}, "ready_for_review", "Review report")
    connector.handle(update(3, "/result " + identity[:8] + " 1"))
    while connector.flush():
        pass
    files = [body for path, body in observed if path.endswith("/sendDocument")]
    assert len(files) == 1 and b"Synthetic public-fixture report" in files[0]
    assert b"report.md" in files[0]


@pytest.mark.parametrize("name", [".env", ".env.local", "credentials.json", ".ssh/id_ed25519", "backup.p12"])
def test_credential_like_deliverables_are_not_uploaded(tmp_path, name):
    observed = []
    store, connector = setup(tmp_path, lambda request: (observed.append(request.url.path) or httpx.Response(200, json={"ok": True, "result": {}})))
    pair(store, connector)
    connector.handle(update(2, "/goal Synthetic protected-file fixture"))
    identity = store.list()[0]["id"]
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("synthetic-private-fixture", encoding="utf-8")
    store.finish(store.claim(), {"outputs": [name]}, "ready_for_review", "Unsafe fixture output")
    connector.handle(update(3, "/result " + identity[:8] + " 1"))
    while connector.flush():
        pass
    assert not any(path.endswith("/sendDocument") for path in observed)
    with store.connect() as db:
        assert db.execute("SELECT state FROM telegram_outbox WHERE artifact IS NOT NULL").fetchone()[0] == "unknown"


def test_changed_deliverable_is_not_uploaded_under_an_old_verification(tmp_path):
    from smara.native_outcomes import output_record
    observed = []
    store, connector = setup(tmp_path, lambda request: (observed.append(request.url.path) or httpx.Response(200, json={"ok": True, "result": {}})))
    pair(store, connector)
    connector.handle(update(2, "/goal Synthetic stale-output fixture"))
    goal = store.claim()
    path = tmp_path / "report.md"
    path.write_text("Synthetic original report")
    saved = output_record(tmp_path, path.name)
    store.finish(goal, {"outputs": [path.name], "outcome": {"outputs": [saved]}}, "ready_for_review", "Review fixture")
    path.write_text("Unexpected replacement")
    connector.handle(update(3, "/result " + goal["id"][:8] + " 1"))
    while connector.flush():
        pass
    assert not any(url.endswith("/sendDocument") for url in observed)


def test_file_changed_during_retrieval_is_not_uploaded(tmp_path, monkeypatch):
    from smara import native_outcomes
    observed = []
    store, connector = setup(tmp_path, lambda request: (observed.append(request.url.path) or httpx.Response(200, json={"ok": True, "result": {}})))
    pair(store, connector)
    connector.handle(update(2, "/goal Synthetic retrieval-race fixture"))
    goal = store.claim()
    path = tmp_path / "report.md"
    path.write_text("Synthetic original")
    store.finish(goal, {"outputs": [path.name]}, "ready_for_review", "Review fixture")
    original = native_outcomes.output_record
    def change_after_check(*args):
        record = original(*args)
        path.write_text("Unexpected replacement")
        return record
    monkeypatch.setattr(native_outcomes, "output_record", change_after_check)
    connector.handle(update(3, "/result " + goal["id"][:8] + " 1"))
    while connector.flush():
        pass
    assert not any(url.endswith("/sendDocument") for url in observed)


def test_uncertain_delivery_remains_visible_after_a_successful_poll(tmp_path, monkeypatch):
    from smara.native_goal_runtime import service_control
    from smara import native_profiles
    store, connector = setup(tmp_path, lambda request: (_ for _ in ()).throw(httpx.ReadTimeout("synthetic timeout", request=request)))
    pair(store, connector)
    connector.flush()
    with store.connect() as db:
        store.set_meta(db, "telegram_health", {"status": "connected"})
    monkeypatch.setattr(native_profiles, "resolve_credential", lambda *args, **kwargs: "synthetic-token-not-a-secret")
    result = service_control(store, {"action": "telegram_status"})
    assert result["health"]["delivery_unknown"] is True
    assert "uncertain" in result["health"]["detail"]
