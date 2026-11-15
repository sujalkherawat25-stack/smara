"""Smara can find a file the owner sent earlier, and its instructions say to edit a sent file in place."""
from datetime import datetime, timezone

from test_agent_db import env, one  # noqa: F401

from src.agent import extras
from src.agent.prompt import BASE
from src.ingest import owner_files


async def test_list_my_files_returns_kept_files_newest_first_with_refs_that_open(env, tmp_path):
    base = str(tmp_path)
    from dataclasses import replace
    env.app.state.settings = replace(env.app.state.settings, owner_files_dir=base)
    ref1 = owner_files.save(base, env.t.id, "bonus112026.xlsx", b"one")
    ref2 = owner_files.save(base, env.t.id, "other.pdf", b"two")
    ctx = type("Ctx", (), {"tenant_id": env.t.id})()
    out = await extras.list_my_files(env.app, ctx, {})
    assert [f["name"] for f in out["files"]] == ["other.pdf", "bonus112026.xlsx"]
    assert out["files"][1]["file_ref"] == ref1 and out["files"][0]["file_ref"] == ref2
    token = ref1.split(":")[1]
    assert owner_files.read(base, env.t.id, token) == b"one"
    only = await extras.list_my_files(env.app, ctx, {"name_contains": "BONUS"})
    assert [f["name"] for f in only["files"]] == ["bonus112026.xlsx"]
    owner_files.delete(base, env.t.id, 1, datetime.now(timezone.utc))
    assert [f["name"] for f in (await extras.list_my_files(env.app, ctx, {}))["files"]] == ["other.pdf"]


def test_the_instructions_say_to_edit_a_sent_file_in_place_and_never_claim_unchecked_totals():
    assert "change THAT file in place" in BASE and "Never rebuild it from a dataframe" in BASE
    assert "mh.compare_workbooks" in BASE and "list_my_files" in BASE
    assert "never say a file is gone before you have checked" in BASE


def test_the_owner_may_open_their_own_kept_files_but_a_customer_session_may_not():
    from src.agent import policy
    known = frozenset({"run_python", "read_attachment", "inspect_and_import_file"})
    owner = policy.SessionCtx(id="s", tenant_id="t", trust="TRUSTED", files=())
    customer = policy.SessionCtx(id="s", tenant_id="t", trust="UNTRUSTED", party_id="p", files=())
    ref = "local:abcdefghijklmnop:bonus.xlsx"
    assert policy.pre_tool_use(owner, "run_python", {"code": "print(1)", "file_refs": [ref]}, known).verdict != "deny"
    assert policy.pre_tool_use(owner, "read_attachment", {"file_ref": ref}, known).verdict != "deny"
    assert policy.pre_tool_use(customer, "run_python", {"code": "print(1)", "file_refs": [ref]}, known).verdict == "deny"
    assert policy.pre_tool_use(owner, "run_python", {"code": "print(1)", "file_refs": ["sandbox:x:y.xlsx"]}, known).verdict == "deny"
    assert policy.pre_tool_use(owner, "run_python", {"code": "print(1)", "file_refs": ["/etc/passwd"]}, known).verdict == "deny"
