"""Whole tasks from the owner's phone to their own PC: queue, pick-up, approvals and codes through the chat, result back."""
import httpx
from fastapi import FastAPI
from test_agent_db import OWNER, env, one  # noqa: F401

from src.agent import policy, tools
from src.agent.policy import SessionCtx
from src.bridge import device, pc_tasks
from src.bridge.pc_tasks import router
from src.config import Settings
from src.ingest.dispatch import dispatch
from src.ingest.payload import InboundMessage

_n = iter(range(1, 10_000))


def api(env):
    app = FastAPI()
    app.state = env.app.state
    app.include_router(router)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://bridge.test")


async def pc(env) -> dict:
    code = await device.start_pairing(env.pool, env.t.id)
    got = await device.complete_pairing(env.pool, code, "Laptop")
    return {"Authorization": f"Bearer {got['token']}"}


def ctx(env, trust="TRUSTED"):
    return SessionCtx("00000000-0000-0000-0000-000000000001", env.t.id, trust)


def texts(env):
    return [m.get("text", {}).get("body") or m.get("interactive", {}).get("body", {}).get("text") for m in env.app.state.wa.sent
            if m.get("to") == OWNER]


def say(text):
    return InboundMessage(wamid=f"pct{next(_n)}", sender=OWNER, kind="text", text=text)


def tap(button):
    return InboundMessage(wamid=f"pct{next(_n)}", sender=OWNER, kind="interactive", button_id=button)


async def test_only_the_owner_can_send_and_only_with_a_connected_pc(env):
    assert "run_on_pc" in tools.TOOL_NAMES
    for trust in ("UNTRUSTED", "SYSTEM"):
        assert policy.pre_tool_use(ctx(env, trust), "run_on_pc", {"task": "x"}, tools.TOOL_NAMES).verdict == "deny"
    assert "Sign in" in (await pc_tasks.run_on_pc(env.app, ctx(env), {"task": "download my GST report"}))["error"]
    env.app.state.settings = Settings(**{**env.app.state.settings.__dict__, "pc_browser_tenants": ""})
    await pc(env)
    assert "cannot use a PC" in (await pc_tasks.run_on_pc(env.app, ctx(env), {"task": "download my GST report"}))["error"]


async def test_the_pc_counts_as_online_only_while_its_task_worker_asks_for_work(env):
    headers = await pc(env)                                     # the device token is in use (the browser helper shares it)
    out = await pc_tasks.run_on_pc(env.app, ctx(env), {"task": "list my Downloads folder"})
    assert out["task_id"] and not out["pc_online"] and "not online" in out["note"]
    async with api(env) as http:
        got = (await http.post("/bridge/tasks/next?wait=0", headers=headers)).json()   # the worker asks: it takes the task
    assert got["task_id"] == out["task_id"]
    assert (await pc_tasks.run_on_pc(env.app, ctx(env), {"task": "list my Desktop folder"}))["pc_online"]
    pc_tasks._worker_polls(env.app)[str(env.t.id)] -= pc_tasks.WORKER_SEEN_S + 1    # the worker stopped asking
    assert not (await pc_tasks.run_on_pc(env.app, ctx(env), {"task": "list my Music folder"}))["pc_online"]


async def test_a_task_goes_to_the_pc_asks_through_the_chat_and_reports_back(env):
    headers = await pc(env)
    async with api(env) as http:
        assert (await http.post("/bridge/tasks/next?wait=0", headers=headers)).json() == {}          # the worker is there
    out = await pc_tasks.run_on_pc(env.app, ctx(env), {"task": "Download the October GST return PDF from the GST portal"})
    assert out["pc_online"] and out["task_id"]
    async with api(env) as http:
        got = (await http.post("/bridge/tasks/next?wait=0", headers=headers)).json()
        assert got["task_id"] == out["task_id"] and "GST" in got["task"]
        assert (await http.post("/bridge/tasks/next?wait=0", headers=headers)).json() == {}          # taken once
        tid = got["task_id"]
        await http.post(f"/bridge/tasks/{tid}/started", json={"goal_id": "g1"}, headers=headers)

        # an approval: a card with Allow / Deny on the phone; the tap goes back to the PC
        await http.post(f"/bridge/tasks/{tid}/ask", json={"request_id": "r1", "summary": "run: python fetch.py"}, headers=headers)
        card = env.app.state.wa.sent[-1]
        assert card["type"] == "interactive" and "python fetch.py" in card["interactive"]["body"]["text"]
        ask_id = card["interactive"]["action"]["buttons"][0]["reply"]["id"].split(":")[1]
        assert (await http.post("/bridge/tasks/answers", headers=headers)).json()["answers"] == []    # nothing yet
        await dispatch(env.app, tap(f"pc_ok:{ask_id}"))
        assert "Allowed" in texts(env)[-1]
        ans = (await http.post("/bridge/tasks/answers", headers=headers)).json()["answers"]
        assert ans == [{"task_id": tid, "request_id": "r1", "kind": "approval", "answer": "allow"}]
        assert (await http.post("/bridge/tasks/answers", headers=headers)).json()["answers"] == []    # handed over once

        # a one-time code: asked in the chat, the owner's reply goes to the PC (and is not logged or kept)
        await http.post(f"/bridge/tasks/{tid}/ask", json={"request_id": "r2", "kind": "otp", "summary": "GST portal login"},
                        headers=headers)
        assert "one-time code" in texts(env)[-1]
        await dispatch(env.app, say("482913"))
        assert texts(env)[-1] == "Code sent to your PC."
        ans = (await http.post("/bridge/tasks/answers", headers=headers)).json()["answers"]
        assert ans[0]["answer"] == "482913" and ans[0]["kind"] == "otp"
        assert (await one(env.pool, "select count(*) n from pc_task_asks where answer = '482913'"))["n"] == 0
        assert (await one(env.pool, "select count(*) n from chat_messages where content like '%%482913%%'"))["n"] == 0

        await http.post(f"/bridge/tasks/{tid}/result", json={"state": "done", "summary": "Saved GSTR3B_Oct.pdf"}, headers=headers)
        assert "Done on your PC" in texts(env)[-1] and "GSTR3B_Oct.pdf" in texts(env)[-1]
        assert (await one(env.pool, "select status from pc_tasks where id = %s", tid))["status"] == "done"


async def test_another_shops_pc_cannot_see_or_answer_this_shops_tasks(env):
    await pc(env)
    out = await pc_tasks.run_on_pc(env.app, ctx(env), {"task": "organise my invoices folder"})
    code = await device.start_pairing(env.pool, env.other.id)
    other = {"Authorization": f"Bearer {(await device.complete_pairing(env.pool, code, 'Other PC'))['token']}"}
    async with api(env) as http:
        assert (await http.post("/bridge/tasks/next?wait=0", headers=other)).json() == {}
        r = await http.post(f"/bridge/tasks/{out['task_id']}/result", json={"state": "done", "summary": "x"}, headers=other)
        assert r.status_code == 404
        assert (await http.post("/bridge/tasks/next?wait=0")).status_code == 401


async def test_a_plain_number_is_a_normal_message_when_no_code_was_asked(env):
    from test_agent_db import ScriptLLM, say as llm_say
    env.app.state.llm = ScriptLLM(llm_say("ok"))
    await dispatch(env.app, say("482913"))
    assert texts(env)[-1] != "Code sent to your PC."


PC_ASK = ("Make a one page PDF summarizing the dues in dues.csv from downloads and save it to the phone task folder. "
          "Don't send the file to anyone.")


async def test_a_job_sent_to_the_pc_is_not_also_done_here(env):
    """The real case: "PDF" and "send" (in "don't send") made our own self-check order a second copy after the hand-off."""
    from test_agent_db import ScriptLLM, call, say as llm_say
    headers = await pc(env)
    llm = env.app.state.llm = ScriptLLM(call("run_on_pc", task="make the dues PDF on the laptop"),
                                        llm_say("Your laptop is on it. The result will come here."))
    await dispatch(env.app, say(PC_ASK))
    assert len(llm.seen) == 2                       # no self-check turned the reply back
    assert texts(env)[-1] == "Your laptop is on it. The result will come here."
    async with api(env) as http:
        assert (await http.post("/bridge/tasks/next?wait=0", headers=headers)).json()["task"] == "make the dues PDF on the laptop"


async def test_doing_it_here_after_the_hand_off_is_turned_back(env):
    from test_agent_db import ScriptLLM, call, say as llm_say
    await pc(env)
    llm = env.app.state.llm = ScriptLLM(call("run_on_pc", task="make the dues PDF on the laptop"),
                                        call("run_python", code="print(1)"),
                                        llm_say("I have sent you the PDF file."), llm_say("Your laptop is still working on it."))
    await dispatch(env.app, say(PC_ASK))
    seen = [m["content"] for m in llm.seen[-1] if m["role"] in ("tool", "user")]
    assert any("on the owner's PC now" in c for c in seen)
    assert any("nothing was sent from here" in c for c in seen) and not any("send_file_to_owner" in c for c in seen)
    assert texts(env)[-1] == "Your laptop is still working on it."


def test_the_block_ends_with_the_owners_next_message_and_needs_a_real_hand_off():
    from src.agent_loop import handed_to_pc, owes_file
    asked = {"role": "user", "content": PC_ASK}
    made = {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "function": {"name": "run_on_pc", "arguments": "{}"}}]}
    queued = {"role": "tool", "tool_call_id": "c1", "content": '{"task_id": "t1", "pc_online": true}'}
    refused = {"role": "tool", "tool_call_id": "c1", "content": '{"error": "no PC is connected to this account."}'}
    note = {"role": "user", "content": "[system note] anything"}
    assert handed_to_pc([asked, made, queued, note]) and not owes_file([asked, made, queued])
    assert not handed_to_pc([asked, made, refused]) and owes_file([asked, made, refused])      # no PC: still done here
    again = {"role": "user", "content": "send me a PDF of the same here too"}
    assert not handed_to_pc([asked, made, queued, again]) and owes_file([asked, made, queued, again])


def test_saying_the_job_was_sent_is_not_a_claim_that_a_file_was_sent():
    from src.agent_loop import unbacked_claim
    turn = [{"role": "user", "content": PC_ASK},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "function": {"name": "run_on_pc", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": '{"task_id": "t1", "pc_online": true}'}]
    fine = ("Laptop on hai — task bhej diya hai. PDF ban jaye to naam, total aur saved path yahin bhej dungi. Kisi ko send "
            "nahi hoga, sirf aapke folder mein save hoga.",          # the real reply that was wrongly held back
            "Task laptop pe bhej diya hai. PDF ka path yahin aayega.",
            "Laptop pe bhej diya. PDF wahin save hogi.",
            "I've sent the task. The PDF will be saved on your laptop.",
            "Sent to your laptop. PDF will follow in this chat.",
            "Your job has been sent. File details will come here.")
    for reply in fine:
        assert unbacked_claim(turn, reply) == "", reply
    false_claims = ("PDF bhej diya hai.", "I have sent you the PDF file.", "Task ho gaya, PDF bhej di.",
                    "Task bhej diya hai. PDF bhi aapko bhej di.", "I made the PDF and sent it to your laptop.",
                    "Sent the task. I have also sent you the Excel file.")
    for reply in false_claims:
        assert "nothing was sent from here" in unbacked_claim(turn, reply), reply
