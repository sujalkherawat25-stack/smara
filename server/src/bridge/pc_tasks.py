"""Whole tasks from the owner's phone to their own PC.

The owner writes on WhatsApp or Telegram; when the job needs their laptop (its files, apps, logins or own browser) the
hosted agent calls run_on_pc. The task waits here until Smara Desktop's background worker on that PC (signed in with the
device token) picks it up, runs it with the desktop agent at full speed, and reports back. While it runs, every approval
the desktop agent needs comes to the owner's chat as an Allow / Deny card, and a one-time code (OTP) it needs is asked in
the chat and typed in on the PC. Reminders and scheduled jobs stay on the hosted side. Nothing here gives the server
access to the PC: the PC connects out and fetches only its own shop's tasks and answers."""
import asyncio
import logging
import re

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from src import tenants
from src.bridge import device
from src.loop.owner_channel import send_to_owner
from src.loop.whatsapp import text_message

log = logging.getLogger(__name__)
router = APIRouter(prefix="/bridge/tasks")

EXPIRE_HOURS = 6          # a task the PC never picked up is dropped after this (the owner is told)
POLL_S = 20
OTP = re.compile(r"^\s*(\d{4,8})\s*$")
ASK_FRESH_MINUTES = 15    # an OTP question older than this no longer captures the owner's next number


class Started(BaseModel):
    goal_id: str = Field(max_length=128)


class Ask(BaseModel):
    request_id: str = Field(min_length=1, max_length=128)
    kind: str = Field(default="approval", pattern="^(approval|otp)$")
    summary: str = Field(min_length=1, max_length=1500)


class Result(BaseModel):
    state: str = Field(pattern="^(done|failed)$")
    summary: str = Field(default="", max_length=6000)


# ------------------------------------------------------------------ the hosted agent's side

async def queue(pool, tenant_id: str, task: str) -> dict:
    async with pool.connection() as conn:
        row = await (await conn.execute(
            "INSERT INTO pc_tasks (tenant_id, task) VALUES (%s, %s) RETURNING id", (tenant_id, task))).fetchone()
    return {"task_id": str(row["id"])}


async def run_on_pc(app, ctx, args: dict) -> dict:
    """Agent tool: send the whole task to the owner's own PC."""
    task = " ".join(str(args.get("task") or "").split())
    if not 3 <= len(task) <= 4000:
        return {"error": "describe the task for the PC in 3-4000 characters"}
    pool = app.state.pool
    from src.config import pc_allowed
    if not pc_allowed(app.state.settings, ctx.tenant_id):
        return {"error": "this account cannot use a PC yet; do the task here with your own tools"}
    if await device.paired_device(pool, ctx.tenant_id) is None:
        return {"error": "no PC is connected to this account. Tell the owner: open Smara Desktop on the laptop and press "
                         "Sign in (one tap in this chat). Then ask again."}
    async with pool.connection() as conn:
        busy = (await (await conn.execute(
            "SELECT count(*) n FROM pc_tasks WHERE tenant_id = %s AND status IN ('queued', 'running', 'waiting')",
            (ctx.tenant_id,))).fetchone())["n"]
    if busy >= 3:
        return {"error": "three PC tasks are already waiting or running; tell the owner to wait for those first"}
    out = await queue(pool, ctx.tenant_id, task)
    online = await device.online_device(pool, ctx.tenant_id)
    return {**out, "pc_online": bool(online),
            "note": ("Sent to the owner's PC; it starts within seconds. Tell the owner in one line that their laptop is on it and "
                     "that approvals and the result will come here. Do not do the task yourself as well." if online else
                     f"The PC is not online right now. The task waits up to {EXPIRE_HOURS} hours and starts when the laptop is on "
                     "with Smara Desktop running. Tell the owner that, in one line.")}


async def _owner(app, tenant_id: str):
    return await tenants.by_id(app.state.pool, tenant_id)


async def _say(app, tenant_id: str, text: str) -> None:
    t = await _owner(app, tenant_id)
    if t is not None:
        await send_to_owner(app, t, text_message(t.owner_phone, text), kind="owner_text")


async def answer_button(app, tenant_id: str, ask_id: str, allow: bool) -> str:
    """The owner tapped Allow or Deny on a PC approval card."""
    async with app.state.pool.connection() as conn:
        row = await (await conn.execute(
            """UPDATE pc_task_asks SET answer = %s, answered_at = now()
                WHERE id::text = %s AND tenant_id = %s AND kind = 'approval' AND answered_at IS NULL RETURNING id""",
            ("allow" if allow else "deny", ask_id, tenant_id))).fetchone()
    if row is None:
        return "That was already answered."
    return "Allowed. Your PC continues." if allow else "Denied. Your PC will not do that step."


async def capture_otp(app, tenant_id: str, text: str) -> str | None:
    """The owner's next message is the one-time code the PC asked for: it goes to the PC, never to the agent or the log."""
    m = OTP.match(text or "")
    if not m:
        return None
    async with app.state.pool.connection() as conn:
        row = await (await conn.execute(
            """UPDATE pc_task_asks SET answer = %s, answered_at = now()
                WHERE id = (SELECT id FROM pc_task_asks WHERE tenant_id = %s AND kind = 'otp' AND answered_at IS NULL
                              AND created_at > now() - make_interval(mins => %s) ORDER BY created_at DESC LIMIT 1)
            RETURNING id""", (m.group(1), tenant_id, ASK_FRESH_MINUTES))).fetchone()
    return "Code sent to your PC." if row else None


# ------------------------------------------------------------------ the PC's side (device token)

async def _device(request: Request, authorization: str | None) -> dict:
    token = (authorization or "").removeprefix("Bearer ").strip()
    dev = await device.device_for_token(request.app.state.pool, token) if token else None
    if dev is None:
        raise HTTPException(status_code=401, detail="unknown or revoked device")
    return dev


async def _claim(pool, tenant_id) -> dict | None:
    async with pool.connection() as conn:
        async with conn.transaction():
            gone = await (await conn.execute(
                """UPDATE pc_tasks SET status = 'expired', updated_at = now() WHERE tenant_id = %s AND status = 'queued'
                      AND created_at < now() - make_interval(hours => %s) RETURNING task""", (tenant_id, EXPIRE_HOURS))).fetchall()
            row = await (await conn.execute(
                """UPDATE pc_tasks SET status = 'running', started_at = now(), updated_at = now()
                    WHERE id = (SELECT id FROM pc_tasks WHERE tenant_id = %s AND status = 'queued' ORDER BY created_at
                                 LIMIT 1 FOR UPDATE SKIP LOCKED) RETURNING id, task, created_at""", (tenant_id,))).fetchone()
    return {"expired": [r["task"] for r in gone], "task": row}


@router.post("/next")
async def next_task(request: Request, authorization: str | None = Header(default=None), wait: int = POLL_S):
    dev = await _device(request, authorization)
    app, tid = request.app, str(dev["tenant_id"])
    deadline = asyncio.get_running_loop().time() + max(0, min(wait, POLL_S))
    while True:
        got = await _claim(app.state.pool, tid)
        for task in got["expired"]:
            await _say(app, tid, f"Your PC was not on for {EXPIRE_HOURS} hours, so this task was dropped: \"{task[:200]}\". "
                                 "Ask again when the laptop is on.")
        if got["task"]:
            t = got["task"]
            return {"task_id": str(t["id"]), "task": t["task"], "created_at": t["created_at"].isoformat()}
        if asyncio.get_running_loop().time() >= deadline:
            return {}
        await asyncio.sleep(1)


async def _task(pool, tid: str, task_id: str) -> dict:
    async with pool.connection() as conn:
        row = await (await conn.execute("SELECT * FROM pc_tasks WHERE id::text = %s AND tenant_id = %s", (task_id, tid))).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="no such task")
    return row


@router.post("/{task_id}/started")
async def started(task_id: str, body: Started, request: Request, authorization: str | None = Header(default=None)):
    dev = await _device(request, authorization)
    await _task(request.app.state.pool, str(dev["tenant_id"]), task_id)
    async with request.app.state.pool.connection() as conn:
        await conn.execute("UPDATE pc_tasks SET goal_id = %s, updated_at = now() WHERE id::text = %s", (body.goal_id, task_id))
    return {"ok": True}


@router.post("/{task_id}/ask")
async def ask(task_id: str, body: Ask, request: Request, authorization: str | None = Header(default=None)):
    dev = await _device(request, authorization)
    app, tid = request.app, str(dev["tenant_id"])
    await _task(app.state.pool, tid, task_id)
    async with app.state.pool.connection() as conn:
        row = await (await conn.execute(
            """INSERT INTO pc_task_asks (tenant_id, task_id, request_id, kind, summary) VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (task_id, request_id) DO NOTHING RETURNING id""",
            (tid, task_id, body.request_id, body.kind, body.summary))).fetchone()
        await conn.execute("UPDATE pc_tasks SET status = 'waiting', updated_at = now() WHERE id::text = %s", (task_id,))
    if row is None:
        return {"ok": True, "repeat": True}           # asked before: the owner already has the card
    t = await _owner(app, tid)
    if t is None:
        return {"ok": False}
    if body.kind == "otp":
        await send_to_owner(app, t, text_message(t.owner_phone, f"Your PC needs a one-time code: {body.summary[:400]}\n"
                                                                "Reply here with just the code (it goes straight to your PC)."),
                            kind="owner_text")
    else:
        aid = str(row["id"])
        await send_to_owner(app, t, {"messaging_product": "whatsapp", "to": t.owner_phone, "type": "interactive", "interactive": {
            "type": "button", "body": {"text": ("Your PC asks to: " + body.summary)[:1024]},
            "action": {"buttons": [{"type": "reply", "reply": {"id": f"pc_ok:{aid}", "title": "Allow"}},
                                   {"type": "reply", "reply": {"id": f"pc_no:{aid}", "title": "Deny"}}]}}}, kind="owner_text")
    return {"ok": True}


@router.post("/answers")
async def answers(request: Request, authorization: str | None = Header(default=None)):
    """Answers the owner gave that this PC has not collected yet. A code is handed over once and then deleted."""
    dev = await _device(request, authorization)
    async with request.app.state.pool.connection() as conn:
        async with conn.transaction():
            rows = await (await conn.execute(
                """UPDATE pc_task_asks SET delivered_at = now(), answer = CASE WHEN kind = 'otp' THEN NULL ELSE answer END
                    WHERE tenant_id = %s AND answered_at IS NOT NULL AND delivered_at IS NULL
                RETURNING task_id, request_id, kind,
                          (SELECT a.answer FROM pc_task_asks a WHERE a.id = pc_task_asks.id) AS answer""",
                (dev["tenant_id"],))).fetchall()
            await conn.execute(
                """UPDATE pc_tasks SET status = 'running', updated_at = now() WHERE tenant_id = %s AND status = 'waiting'
                      AND NOT EXISTS (SELECT 1 FROM pc_task_asks a WHERE a.task_id = pc_tasks.id AND a.answered_at IS NULL)""",
                (dev["tenant_id"],))
    return {"answers": [{"task_id": str(r["task_id"]), "request_id": r["request_id"], "kind": r["kind"], "answer": r["answer"]}
                        for r in rows]}


@router.post("/{task_id}/result")
async def result(task_id: str, body: Result, request: Request, authorization: str | None = Header(default=None)):
    dev = await _device(request, authorization)
    app, tid = request.app, str(dev["tenant_id"])
    row = await _task(app.state.pool, tid, task_id)
    if row["status"] in ("done", "failed", "expired", "cancelled"):
        return {"ok": True, "repeat": True}
    async with app.state.pool.connection() as conn:
        await conn.execute("UPDATE pc_tasks SET status = %s, result = %s, updated_at = now() WHERE id::text = %s",
                           (body.state, body.summary[:6000], task_id))
        await conn.execute("UPDATE pc_task_asks SET answered_at = now() WHERE task_id::text = %s AND answered_at IS NULL", (task_id,))
    head = "Done on your PC" if body.state == "done" else "Your PC could not finish this"
    await _say(app, tid, f"{head}: \"{row['task'][:120]}\"\n\n{body.summary[:3500] or '(no details)'}")
    return {"ok": True}
