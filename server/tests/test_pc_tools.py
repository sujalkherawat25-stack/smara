"""PC tools over the paired helper: the Smara Shared folder (list, read, write), the policy around it, and the whole trip
from a WhatsApp turn through the job queue to the helper and back."""
import asyncio
import sys
from pathlib import Path

import pytest
from test_agent_db import ScriptLLM, call, env, one, owner, say  # noqa: F401
from test_bridge_db import api, pair

from src.agent.policy import SessionCtx, pre_tool_use
from src.agent.tools import TOOL_NAMES
from src.ingest.dispatch import dispatch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bridge"))
import smara_bridge  # noqa: E402


@pytest.fixture
def shared(tmp_path, monkeypatch):
    monkeypatch.setattr(smara_bridge, "SHARED_DIR", tmp_path / "Shared")
    return tmp_path / "Shared"


# ------------------------------------------------------------ the helper's folder tool

def test_write_then_list_then_read(shared):
    assert smara_bridge.run_tool({"tool": "files", "action": "write", "path": "bills/oct.csv", "text": "a,b\n1,2\n"})["bytes"] == 8
    assert smara_bridge.run_tool({"tool": "files", "action": "list", "path": ""})["entries"] == [{"name": "bills/", "bytes": None}]
    assert smara_bridge.run_tool({"tool": "files", "action": "read", "path": "bills/oct.csv"})["text"] == "a,b\n1,2\n"


def test_nothing_outside_the_shared_folder_can_be_touched(shared):
    outside = shared.parent / "secret.txt"
    outside.write_text("private")
    for path in ("../secret.txt", "..\\secret.txt", "a/../../secret.txt", str(outside), "C:\\Windows\\win.ini"):
        for action in ("read", "write", "list"):
            out = smara_bridge.run_tool({"tool": "files", "action": action, "path": path, "text": "x"})
            assert out.get("error"), (path, action, out)
    assert outside.read_text() == "private"


def test_a_file_is_never_replaced_unless_asked_and_binary_is_refused(shared):
    smara_bridge.run_tool({"tool": "files", "action": "write", "path": "n.txt", "text": "one"})
    assert "already exists" in smara_bridge.run_tool({"tool": "files", "action": "write", "path": "n.txt", "text": "two"})["error"]
    assert (shared / "n.txt").read_text() == "one"
    assert smara_bridge.run_tool({"tool": "files", "action": "write", "path": "n.txt", "text": "two", "overwrite": True})["bytes"] == 3
    (shared / "pic.bin").write_bytes(b"\x89PNG\x00\x01")
    assert "binary" in smara_bridge.run_tool({"tool": "files", "action": "read", "path": "pic.bin"})["error"]


def test_an_unknown_tool_is_an_error_not_a_crash(shared):
    assert "no tool" in smara_bridge.run_tool({"tool": "shell", "cmd": "dir"})["error"]


# ------------------------------------------------------------ policy

def test_reads_run_writes_ask_and_customers_get_nothing():
    v = lambda ctx, a: pre_tool_use(ctx, "pc_files", a, TOOL_NAMES).verdict
    owner_ctx = SessionCtx(id="s", tenant_id="t", trust="TRUSTED")
    assert "pc_files" in TOOL_NAMES
    assert v(owner_ctx, {"action": "list"}) == "allow"
    assert v(owner_ctx, {"action": "read", "path": "a.txt"}) == "allow"
    assert v(owner_ctx, {"action": "write", "path": "a.txt", "text": "x"}) == "approve"
    assert v(owner_ctx, {"action": "delete"}) == "deny"
    for trust in ("UNTRUSTED", "SYSTEM"):
        assert v(SessionCtx(id="s", tenant_id="t", trust=trust), {"action": "list"}) == "deny"


# ------------------------------------------------------------ whole trip

async def helper_loop(http, token, stop: asyncio.Event):
    """What the PC helper does for a tool job: poll, run it, post the result."""
    h = {"Authorization": f"Bearer {token}"}
    while not stop.is_set():
        job = (await http.post("/bridge/poll", params={"wait": 1}, headers=h)).json()
        if job:
            await http.post("/bridge/result", json={"job_id": job["job_id"], "result": smara_bridge.run_tool(job["command"])}, headers=h)


async def test_whatsapp_turn_reaches_the_pc_folder_and_a_write_waits_for_the_tap(env, shared):
    (shared / "notes.txt").parent.mkdir(parents=True, exist_ok=True)
    (shared / "notes.txt").write_text("call Ravi")
    async with api(env) as http:
        token, _ = await pair(env, http)
        stop = asyncio.Event()
        helper = asyncio.create_task(helper_loop(http, token, stop))
        try:
            env.app.state.llm = ScriptLLM(
                call("pc_files", action="list"),
                call("pc_files", action="read", path="notes.txt"),
                call("pc_files", action="write", path="reply.txt", text="Paid on Friday"),
                say("Saved."))
            await dispatch(env.app, owner("p1", "what is in my Smara folder, then save a reply there"))
            assert not (shared / "reply.txt").exists()                       # waiting for the owner's tap
            tool_msgs = [json.loads(m["content"]) for m in env.app.state.llm.calls[-1] if m.get("role") == "tool"] \
                if hasattr(env.app.state.llm, "calls") else []
            sid = (await one(env.pool, "select id from agent_sessions where status = 'WAITING_APPROVAL'"))["id"]
            assert "reply.txt" in env.app.state.wa.sent[-1]["interactive"]["body"]["text"]
            await dispatch(env.app, owner("p2", button=f"ag_ok:{sid}"))
            assert (shared / "reply.txt").read_text() == "Paid on Friday"
        finally:
            stop.set()
            await helper


async def test_without_a_paired_pc_the_tool_says_so_and_nothing_is_queued(env, shared):
    env.app.state.llm = ScriptLLM(call("pc_files", action="list"), say("No PC."))
    await dispatch(env.app, owner("p3", "what is in my Smara folder"))
    assert (await one(env.pool, "select count(*) as n from bridge_jobs"))["n"] == 0
