"""Owner commands that use the browser box.

  gstin <number>     offline check, instant
  quotes <product>   read-only IndiaMART lookup, runs in the background
  browse <task>      LLM plans steps -> owner approves -> runs (invariant 3)
"""
import asyncio
import json
import logging

from src.box.client import run_box
from src.box.gstin import check_gstin_status
from src.box.guard import describe_plan, is_mutating, validate_plan
from src.loop.whatsapp import text_message

log = logging.getLogger(__name__)

PLAN_SYSTEM = (
    "Turn the user's request into a browser plan. Return JSON {\"plan\": [steps]}. Steps: "
    "{\"action\":\"goto\",\"url\":...} | {\"action\":\"click\",\"selector\":...} | "
    "{\"action\":\"fill\",\"selector\":...,\"value\":...} | {\"action\":\"press\",\"value\":\"Enter\"} | "
    "{\"action\":\"wait\",\"value\":ms} | {\"action\":\"extract_text\",\"selector\":...}. "
    "Start with goto, at most 15 steps, CSS selectors only. Never include passwords, OTPs or payment details."
)


async def _send(app, tenant_id: str, payload: dict, number_id, channel) -> None:
    """Reply on the channel the owner used (Telegram or WhatsApp)."""
    from src import tenants
    from src.loop.owner_channel import send_to_owner

    t = await tenants.by_id(app.state.pool, tenant_id)
    if t:
        await send_to_owner(app, t, payload, kind="owner_text", reply_channel=channel, number_id=number_id or None)
    else:
        await app.state.wa.send(payload, number_id=number_id)


def spawn(app, coro) -> None:
    task = asyncio.create_task(coro)
    app.state.bg.add(task)
    task.add_done_callback(app.state.bg.discard)


def format_quotes(res: dict) -> str:
    if res.get("error"):
        return f"Quote lookup failed: {res['error']}"
    if not res["quotes"]:
        return f"No supplier listings found for '{res['product']}'."
    rows = [f"{i}. {q['title']}" + (f" - {q['price']}" if q["price"] else "")
            + (f" ({q['location']})" if q["location"] else "") for i, q in enumerate(res["quotes"], 1)]
    return f"IndiaMART for '{res['product']}' (unverified listings):\n" + "\n".join(rows)


def format_task(res: dict) -> str:
    if res.get("error"):
        return f"Browser task failed: {res['error']}"
    out = [f"Done. Ended on: {res['title'][:80]} ({res['url'][:100]})"]
    out += [f"> {t[:600]}" for t in res["extracted"]]
    return "\n".join(out)


async def _quotes(app, tenant_id: str, phone: str, product: str, number_id, channel) -> None:
    res = await run_box("scrape_indiamart_quotes", {"product": product})
    await _send(app, tenant_id, text_message(phone, format_quotes(res)), number_id, channel)


async def handle_text(app, tenant_id: str, phone: str, text: str, number_id: str | None = None,
                      channel: str = "whatsapp") -> str | None:
    cmd, _, arg = text.strip().partition(" ")
    cmd, arg = cmd.lower(), arg.strip()
    if cmd == "gstin" and arg:
        r = check_gstin_status(arg)
        if not r["valid"]:
            return f"GSTIN invalid: {r['reason']}."
        return (f"GSTIN {r['gstin']} is well-formed (state {r['state_code']}, PAN {r['pan']}). "
                f"Registration status needs the portal captcha: {r['portal_url']}")
    if cmd == "quotes" and arg:
        spawn(app, _quotes(app, tenant_id, phone, arg, number_id, channel))
        return f"Looking up '{arg}' on IndiaMART..."
    if cmd == "browse" and arg:
        return await _propose(app, tenant_id, phone, arg, number_id, channel)
    return None


async def _propose(app, tenant_id: str, phone: str, instruction: str, number_id, channel) -> str | None:
    if not app.state.llm.enabled:
        return "Browser tasks need the LLM configured to plan steps."
    try:
        plan = validate_plan((await app.state.llm.json(PLAN_SYSTEM, instruction)).get("plan"))
    except Exception as e:
        return f"I could not make a safe plan for that ({e})."
    async with app.state.pool.connection() as conn:
        cur = await conn.execute(
            "INSERT INTO browser_tasks (tenant_id, instruction, plan) VALUES (%s, %s, %s) RETURNING id",
            (tenant_id, instruction, json.dumps(plan)))
        tid = str((await cur.fetchone())["id"])
    note = "\nIncludes clicking/typing on the site." if is_mutating(plan) else ""
    btn = lambda k, t: {"type": "reply", "reply": {"id": f"{k}:{tid}", "title": t}}
    await _send(app, tenant_id, {
        "messaging_product": "whatsapp", "to": phone, "type": "interactive", "interactive": {
            "type": "button",
            "body": {"text": f"Run this browser task?\n\"{instruction[:150]}\"\n\n{describe_plan(plan)}{note}"[:1024]},
            "action": {"buttons": [btn("bt_approve", "Approve"), btn("bt_reject", "Reject")]}}}, number_id, channel)
    return None


async def decide_browser_task(app, tenant_id: str, phone: str, task_id: str, approve: bool,
                              number_id: str | None = None, channel: str = "whatsapp") -> str:
    async with app.state.pool.connection() as conn:
        cur = await conn.execute(
            """UPDATE browser_tasks SET status = %s, decided_at = now()
                WHERE id = %s AND tenant_id = %s AND status = 'PENDING' RETURNING plan""",
            ("APPROVED" if approve else "REJECTED", task_id, tenant_id))
        row = await cur.fetchone()
    if row is None:
        return "That task was already handled or no longer exists."
    if not approve:
        return "Okay, cancelled."
    spawn(app, _execute(app, tenant_id, phone, task_id, row["plan"], number_id, channel))
    return "Running it now..."


async def _execute(app, tenant_id: str, phone: str, task_id: str, plan: list, number_id, channel) -> None:
    res = await run_box("run_personal_browser_task", {"plan": plan})
    async with app.state.pool.connection() as conn:
        await conn.execute("UPDATE browser_tasks SET status = %s, result = %s WHERE id = %s",
                           ("FAILED" if res.get("error") else "DONE", json.dumps(res), task_id))
    await _send(app, tenant_id, text_message(phone, format_task(res)), number_id, channel)
