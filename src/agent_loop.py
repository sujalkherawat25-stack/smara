"""Smara's agent loop: every event starts (or resumes) a multi-turn tool loop.

    event -> session(messages) -> LLM -> tool_calls -> PreToolUse -> execute | deny | gate
          ^                                                         |
          └──────────── tool results fed back (self-verification) ──┘

A gated call (T2/T3) freezes the session in Postgres with the exact call and
any calls after it, shows the owner a card, and stops. The owner's tap is
handled deterministically (never by the model) and resumes from that call.
"""
import asyncio
import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
import logging
from datetime import date, datetime, timedelta, timezone

from src import clock, tenants
from src.agent import actions, extras, memory_pass, tools, web, reader
from src.agent.context import compact
from src.agent.policy import SessionCtx, approval_text, pre_tool_use
from src.agent.prompt import build_system
from src.loop.llm import RateLimited
from src.loop.owner_channel import send_to_owner
from src.loop.whatsapp import text_message

log = logging.getLogger(__name__)
RATE_RETRY_S = 25         # the model is busy (rate limited): wait and try the same step again
RATE_RETRIES = 4
MAX_STEPS = 40          # model calls per turn (each owner message / resume gets a fresh budget)
BROWSER_RESULT_CHARS = 20000   # one page: what changed + every numbered control + some text
CONTINUE_WITHIN = timedelta(minutes=30)
EXECUTORS = {"query_ledger_sql": tools.query_ledger_sql, "resolve_continuum_memory": tools.resolve_continuum_memory,
             "write_continuum_evidence": tools.write_continuum_evidence,
             "inspect_and_import_file": actions.inspect_and_import_file, "read_attachment": reader.read_attachment, "run_python": actions.run_python,
             "get_current_time": extras.get_current_time, "schedule_followup": extras.schedule_followup,
             "list_followups": extras.list_followups, "cancel_followup": extras.cancel_followup,
             "check_gstin": extras.check_gstin, "build_payment_request": extras.build_payment_request,
             "send_file_to_owner": extras.send_file_to_owner, "web_search": web.web_search}


def _spawn(app, coro) -> None:
    task = asyncio.create_task(coro)
    app.state.bg.add(task)
    task.add_done_callback(app.state.bg.discard)


def active_grants(row) -> tuple[str, ...]:
    now = datetime.now(timezone.utc).isoformat()
    return tuple(g["host"] for g in (row.get("grants") or []) if g.get("until", "") > now)


def host_of(page: dict) -> str:
    return (urlparse((page or {}).get("url", "")).hostname or "").lower()


def page_text(page: dict) -> str:
    return f"{(page or {}).get('url', '')} {(page or {}).get('title', '')}".lower()


def last_fill(messages: list[dict]) -> tuple[str, str]:
    """(ref, value) of the agent's most recent browser 'fill' in this conversation."""
    for m in reversed(messages):
        for c in reversed(m.get("tool_calls") or []):
            if c["function"]["name"] == "browser_step":
                try:
                    a = json.loads(c["function"].get("arguments") or "{}")
                except ValueError:
                    continue
                if a.get("action") == "fill":
                    return str(a.get("ref", a.get("selector", ""))), str(a.get("value", ""))
                if a.get("action") == "navigate":
                    return "", ""
    return "", ""


def _ctx(row, call_id: str = "", browsers=None) -> SessionCtx:
    sid = str(row["id"])
    return SessionCtx(id=sid, tenant_id=str(row["tenant_id"]), trust=row["trust"],
                      party_id=str(row["party_id"]) if row["party_id"] else None, files=tuple(row["files"]), call_id=call_id,
                      grants=active_grants(row), host=host_of(browsers.page(sid)) if browsers else "",
                      on_device=bool(browsers) and getattr(browsers, "where", lambda _: "")(sid) == "device",
                      page=page_text(browsers.page(sid)) if browsers else "",
                      controls=str((browsers.page(sid) or {}).get("page", "")) if browsers else "",
                      last_fill=last_fill(row.get("messages") or [])[0], last_fill_value=last_fill(row.get("messages") or [])[1])


async def _record_usage(pool, row, usage: dict | None) -> None:
    """Best effort: metering must never break a conversation."""
    if not usage or not usage.get("total"):
        return
    try:
        async with pool.connection() as conn:
            await conn.execute(
                """INSERT INTO llm_usage (tenant_id, session_id, model, prompt_tokens, completion_tokens, total_tokens)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (row["tenant_id"], row["id"], usage["model"], usage["prompt"], usage["completion"], usage["total"]))
    except Exception:
        log.warning("could not record model usage", exc_info=True)


async def _save(pool, sid, messages, *, status=None, pending=..., steps=None) -> None:
    sets, vals = ["messages = %s", "updated_at = now()"], [json.dumps(messages, default=str)]
    for col, v in (("status", status), ("steps", steps)):
        if v is not None:
            sets.append(f"{col} = %s"), vals.append(v)
    if pending is not ...:
        sets.append("pending = %s"), vals.append(json.dumps(pending) if pending is not None else None)
    async with pool.connection() as conn:
        await conn.execute(f"UPDATE agent_sessions SET {', '.join(sets)} WHERE id = %s", (*vals, sid))


async def start_session(app, *, tenant, trust: str, trigger: str, content: str, party_id=None, files=(),
                        channel: str = "whatsapp", background: bool = True, fallback=None) -> str:
    pool, user = app.state.pool, {"role": "user", "content": content}
    row = None
    if trust == "TRUSTED":   # an owner's follow-up continues the recent conversation
        async with pool.connection() as conn:
            cur = await conn.execute(
                """UPDATE agent_sessions SET status = 'RUNNING', messages = messages || %s::jsonb, files = files || %s,
                          updated_at = now() WHERE id = (SELECT id FROM agent_sessions WHERE tenant_id = %s
                          AND trust = 'TRUSTED' AND status = 'DONE' AND channel = %s AND updated_at > now() - %s
                          ORDER BY updated_at DESC LIMIT 1) RETURNING id""",
                (json.dumps([user]), list(files), tenant.id, channel, CONTINUE_WITHIN))
            row = await cur.fetchone()
    if row is None:
        # Built BEFORE taking a connection: it queries the pool itself, and holding one while asking for
        # another deadlocks the small pool as soon as a few sessions start at once.
        system = {"role": "system", "content": await build_system(app, tenant, trust)}
        async with pool.connection() as conn:
            cur = await conn.execute(
                """INSERT INTO agent_sessions (tenant_id, trust, trigger, party_id, channel, files, messages)
                   VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                (tenant.id, trust, trigger, party_id, channel, list(files), json.dumps([system, user])))
            row = await cur.fetchone()
    sid = str(row["id"])
    job = run(app, sid, fallback=fallback)
    _spawn(app, job) if background else await job
    return sid


HEAVY_TOOLS = frozenset({"run_python", "inspect_and_import_file", "browser_step", "read_attachment"})


def heavy(messages: list[dict]) -> bool:
    """File, spreadsheet and browser work needs the strong model start to finish; only light chat may use the fallback."""
    used = any(c["function"]["name"] in HEAVY_TOOLS for m in messages for c in (m.get("tool_calls") or []))
    attached = any("file_ref=" in str(m.get("content", "")) for m in messages if m["role"] == "user")
    asked = any(_FILE_ASK.search(str(m.get("content", ""))) for m in messages[-6:] if m["role"] == "user")
    return used or attached or asked


_FILE_ASK = re.compile(r"\b(pdf|excel|xlsx)\b", re.I)
_SEND_ASK = re.compile(r"(bhej|send|bana|de do|dedo|chahiye|chahie|mail)", re.I)


def owes_file(messages: list[dict]) -> bool:
    """True when the owner's latest message asked for a PDF/Excel file and this turn never sent one."""
    last = max((i for i, m in enumerate(messages) if m["role"] == "user" and not m["content"].startswith("[system note]")), default=-1)
    if last < 0 or not (_FILE_ASK.search(messages[last]["content"]) and _SEND_ASK.search(messages[last]["content"])):
        return False
    return not any(m["role"] == "tool" and '"sent": true' in m["content"] and '"filename"' in m["content"] for m in messages[last:])


async def run(app, sid: str, fallback=None) -> None:
    pool = app.state.pool
    async with pool.connection() as conn:
        row = await (await conn.execute("SELECT * FROM agent_sessions WHERE id = %s AND status = 'RUNNING'", (sid,))).fetchone()
    if row is None:
        return
    messages, steps, suspended, empties = row["messages"], row["steps"], False, 0
    budget, seen, nudged, busy = 0, {}, False, 0
    try:
        tenant = await tenants.by_id(pool, str(row["tenant_id"]))
        if messages and messages[0]["role"] == "system":      # never carry a stale date/ledger/instructions into a new turn
            messages[0] = {"role": "system", "content": await build_system(app, tenant, row["trust"], str(row["id"]))}
        while budget < MAX_STEPS:
            messages = compact(messages)
            try:
                msg = await app.state.llm.chat(messages, tools.TOOLS, allow_fallback=not heavy(messages))
            except RateLimited:
                busy += 1                   # a busy model is not a failed job: tell the owner once, wait, try the same step again
                if busy > RATE_RETRIES:
                    raise
                if busy == 1 and row["trust"] == "TRUSTED":
                    await _finish(app, row, "Model abhi busy hai, main thodi der mein dobara try karta hoon. Aapko kuch nahi karna.")
                await asyncio.sleep(RATE_RETRY_S)
                continue
            await _record_usage(pool, row, msg.pop("_usage", None))
            steps += 1
            budget += 1
            calls = msg.get("tool_calls") or []
            if not calls and not (msg.get("content") or "").strip():
                empties += 1          # a provider hiccup, not an answer: ask again instead of going silent
                if empties > 2:
                    raise RuntimeError("the model returned empty replies")
                continue
            messages.append({"role": "assistant", "content": msg.get("content") or "", **({"tool_calls": calls} if calls else {})})
            if not calls and not nudged and owes_file(messages[:-1]):
                nudged = True      # self-check: they asked for a file and none went out, so do it now rather than describe it
                messages.append({"role": "user", "content": "[system note] The owner asked for a file and you have not sent one in this "
                                 "turn. Create it with run_python (the file type they asked for) and send it with send_file_to_owner, "
                                 "then confirm. Do this even if you sent a similar file earlier."})
                continue
            if not calls:
                await _save(pool, sid, messages, status="DONE", steps=steps)
                await _finish(app, row, msg.get("content") or "")
                if row["trust"] == "TRUSTED":          # keep what is worth remembering, without slowing the reply
                    _spawn(app, memory_pass.remember_from_turn(app, str(row["tenant_id"]), sid, list(messages)))
                return
            for c in calls:                       # the same call over and over is a loop: tell the model to change approach
                key = (c["function"]["name"], c["function"]["arguments"])
                seen[key] = seen.get(key, 0) + 1
                if seen[key] == 3:
                    messages.append({"role": "user", "content": "[system note] You have made this exact call 3 times with the same "
                                     "result. Change your approach (different tool, different arguments) or explain what is blocking you."})
            if await _process(app, row, messages, calls):
                suspended = True   # keep the browser page alive for the approved step
                return await _save(pool, sid, messages, steps=steps)
            await _save(pool, sid, messages, steps=steps)
        # Out of budget: never go silent. Ask the model for an honest status instead of a dead end.
        messages.append({"role": "user", "content": "[system note] Step budget reached. Without calling any tool, tell the owner "
                         "briefly what you finished, what is left, and what you need from them to continue."})
        summary = ((await app.state.llm.chat(messages, [])).get("content") or "").strip()
        messages.append({"role": "assistant", "content": summary})
        await _save(pool, sid, messages, status="DONE", steps=steps)
        await _finish(app, row, summary or "This took more steps than I can do in one go. Tell me to continue and I will pick it up.")
    except Exception:
        log.exception("agent session %s failed", sid)
        await _save(pool, sid, messages, status="FAILED", steps=steps)
        if fallback and steps <= 1:
            await fallback()   # e.g. the deterministic debtor path, so a claim is never lost
        elif row["trust"] == "TRUSTED":
            await _finish(app, row, "Sorry, I could not finish that right now. Please try again in a minute.")
    # The browser page is NOT closed when a turn ends: the owner's next message in this conversation ("now open her chat")
    # continues on the same page. Idle pages are closed by the browser manager after a while.


async def _process(app, row, messages: list, calls: list) -> bool:
    """Run tool calls in order. Returns True if the session suspended at a gate."""
    tenant = await tenants.by_id(app.state.pool, str(row["tenant_id"]))
    for i, call in enumerate(calls):
        name, cid = call["function"]["name"], call["id"]
        try:
            args = json.loads(call["function"].get("arguments") or "{}")
        except ValueError:
            args = None
        row["messages"] = messages                    # the policy needs this turn's typing, not the saved copy
        ctx = _ctx(row, cid, getattr(app.state, "browsers", None))
        d = pre_tool_use(ctx, name, args, tools.TOOL_NAMES)
        if d.verdict == "deny":
            result = {"error": f"blocked by policy: {d.reason}"}
        elif d.verdict == "allow":
            result = await _execute(app, ctx, tenant, name, args)
            if new := result.pop("_new_files", None):   # sandbox outputs become openable in this session
                row["files"] = [*row["files"], *new]
                async with app.state.pool.connection() as conn:
                    await conn.execute("UPDATE agent_sessions SET files = files || %s WHERE id = %s", (new, ctx.id))
        else:
            err = await actions.validate_gated(app, ctx, name, args)
            if err is None:
                extra = await actions.open_gate(app, ctx, tenant, name, args, approval_text(name, args, getattr(getattr(app.state, "browsers", None), "page", lambda _: None)(ctx.id),
                                                       ctx.last_fill_value if name == "browser_step" else ""))   # args now hold computed amounts
                pending = {"call_id": cid, "name": name, "args": args, "card": extra.get("card", d.card),
                           "remaining": calls[i + 1:], **{k: v for k, v in extra.items() if k != "card"}}
                await _save(app.state.pool, ctx.id, messages, status="WAITING_APPROVAL", pending=pending)
                return True
            result = {"error": err}
        messages.append({"role": "tool", "tool_call_id": cid,
                         "content": tools.bounded(result, BROWSER_RESULT_CHARS if name == "browser_step" else 6000)})
    return False


async def _execute(app, ctx, tenant, name, args) -> dict:
    try:
        if name == "send_whatsapp_message":   # T1: to the owner only (T2 to customers is gated)
            text = text_message(tenant.owner_phone, str(args.get("text", ""))[:3000])
            # The owner just wrote: reply at once. A scheduled job messaging them unprompted is a held-able note.
            kind = "owner_note" if ctx.trust == "SYSTEM" else "owner_text"
            via = await send_to_owner(app, tenant, text, kind=kind)
            return {"sent": via != "held", "to": "owner", "queued_until_owner_reachable": via == "held"}
        if name == "browser_step":
            return await app.state.browsers.step(ctx.id, args, tenant_id=ctx.tenant_id)
        return await EXECUTORS[name](app, ctx, args)
    except Exception as e:
        return {"error": f"{type(e).__name__}: {str(e)[:300]}"}


async def _finish(app, row, text: str) -> None:
    if row["trust"] == "TRUSTED" and text.strip():
        tenant = await tenants.by_id(app.state.pool, str(row["tenant_id"]))
        await send_to_owner(app, tenant, text_message(tenant.owner_phone, text[:4000]), kind="owner_text",
                            reply_channel=row["channel"])


GRANT_FOR = timedelta(minutes=15)


async def grant_site(app, tenant_id: str, sid: str) -> str | None:
    """Owner tapped 'approve and allow this site': the agent may click and type here (nothing sensitive) for 15 minutes."""
    host = host_of(app.state.browsers.page(sid)) if getattr(app.state, "browsers", None) else ""
    if not host:
        return None
    until = (datetime.now(timezone.utc) + GRANT_FOR).isoformat()
    async with app.state.pool.connection() as conn:
        row = await (await conn.execute(
            """UPDATE agent_sessions SET grants = (SELECT coalesce(jsonb_agg(g), '[]'::jsonb) FROM jsonb_array_elements(grants) g
                                                      WHERE g->>'host' <> %(h)s) || jsonb_build_array(jsonb_build_object('host', %(h)s::text, 'until', %(u)s::text))
                WHERE id::text = %(s)s AND tenant_id = %(t)s RETURNING id""", {"h": host, "u": until, "s": sid, "t": tenant_id})).fetchone()
    return host if row else None


async def resume(app, tenant_id: str, *, sid: str | None = None, adjustment_id: str | None = None,
                 approved: bool, outcome: dict | None = None, background: bool = True) -> bool:
    """Owner decided a gated call. Returns False if nothing was waiting (stale or double tap)."""
    where, arg = ("id = %s", sid) if sid else ("pending->>'adjustment_id' = %s", adjustment_id)
    async with app.state.pool.connection() as conn:
        row = await (await conn.execute(
            f"""UPDATE agent_sessions SET status = 'RUNNING', updated_at = now()
                 WHERE {where} AND tenant_id = %s AND status = 'WAITING_APPROVAL' RETURNING *""", (arg, tenant_id))).fetchone()
    if row is None:
        return False
    p, messages = row["pending"], row["messages"]
    ctx, tenant = _ctx(row, p["call_id"], getattr(app.state, "browsers", None)), await tenants.by_id(app.state.pool, tenant_id)
    if outcome is not None:          # propose_ledger_adjustment: the ledger path already applied the decision
        result = outcome
    elif approved and pre_tool_use(ctx, p["name"], p["args"], tools.TOOL_NAMES).verdict in ("approve", "allow"):
        result = await actions.execute_approved(app, ctx, tenant, p["name"], p["args"])
    else:
        await actions.on_rejected(app, ctx, p["name"], p["args"])
        result = {"error": "the owner rejected this action"}
    messages.append({"role": "tool", "tool_call_id": p["call_id"], "content": tools.bounded(result)})
    if await _process(app, row, messages, p.get("remaining") or []):
        return True
    await _save(app.state.pool, ctx.id, messages, pending=None)
    job = run(app, ctx.id)
    _spawn(app, job) if background else await job
    return True
