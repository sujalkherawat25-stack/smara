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
from collections import Counter
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import httpx
import logging
from datetime import date, datetime, timedelta, timezone

from src import chatlog, clock, health_watch, prefs, proactive, tenants
from src.agent import actions, bookkeeping, calc, documents, extras, ageing, bank, gst, memory_pass, pc, reader, register_audit, sheet_tools, skills, tds_check, tools, web
from src.agent.context import compact
from src.bridge import pc_tasks
from src.agent.policy import SessionCtx, approval_text, control, pre_tool_use, typed_into_form
from src.agent.prompt import build_system, latest_owner_text
from src.loop.llm import RateLimited
from src.loop.owner_channel import CURRENT_REPLY_CHANNEL, send_to_owner
from src.loop.whatsapp import text_message

log = logging.getLogger(__name__)

PROGRESS_FIRST_S = 25      # a task that runs this long gets a note, so the owner does not think it is stuck
PROGRESS_EVERY_S = 120
PROGRESS_MAX = 2           # one more if it goes on for a long time; five identical notes read like a fault
PROGRESS_NOTES = ("Still working on it. I'll send the answer as soon as it's ready.",
                  "Still working on it. This one is taking longer than usual.")
# for someone who picked a language; everyone else gets the English notes above
PROGRESS_NOTES_IN = {
    "hindi": ("इस पर काम चल रहा है। जवाब तैयार होते ही भेज दूँगी।", "अभी काम चल रहा है। इसमें थोड़ा ज़्यादा समय लग रहा है।"),
    "hinglish": ("Is par kaam chal raha hai. Jawab taiyar hote hi bhej dungi.",
                 "Abhi kaam chal raha hai. Isme thoda zyada samay lag raha hai."),
}
SEARCH_ENOUGH, SEARCH_PLENTY = 4, 7      # web searches for one request before the model is told to answer (never blocked)
TYPING_EVERY_S = 4.5        # Telegram shows 'typing…' for about 5 seconds per call
RATE_RETRY_S = 25         # the model is busy (rate limited): wait and try the same step again
RATE_RETRIES = 4
MAX_STEPS = 40          # model calls per turn (each owner message / resume gets a fresh budget)
BROWSER_RESULT_CHARS = 20000   # one page: what changed + every numbered control + some text
CONTINUE_WITHIN = timedelta(minutes=30)
EXECUTORS = {"query_ledger_sql": tools.query_ledger_sql, "resolve_continuum_memory": tools.resolve_continuum_memory,
             "write_continuum_evidence": tools.write_continuum_evidence,
             "search_chat_history": tools.search_chat_history,
             "inspect_and_import_file": actions.inspect_and_import_file, "read_attachment": reader.read_attachment, "make_document": documents.make_document, "load_skill": skills.load_skill, "save_skill": skills.save_skill, "delete_skill": skills.delete_skill,"run_python": actions.run_python,
             "get_current_time": extras.get_current_time, "schedule_followup": extras.schedule_followup,
             "list_followups": extras.list_followups, "list_my_files": extras.list_my_files, "cancel_followup": extras.cancel_followup,
             "calculate": calc.calculate, "check_gstin": extras.check_gstin, "build_payment_request": extras.build_payment_request,
             "send_file_to_owner": extras.send_file_to_owner, "react_to_message": extras.react_to_owner, "web_search": web.web_search,
             "pc_files": pc.pc_files, "set_customer_phone": bookkeeping.set_customer_phone,
             "find_customer": bookkeeping.find_customer, "set_reply_style": prefs.set_reply_style, "set_proactive_messages": proactive.set_proactive_messages, "run_on_pc": pc_tasks.run_on_pc,
             "gst_reconcile": gst.gst_reconcile, "bank_reconcile": bank.bank_reconcile, "ageing_report": ageing.ageing_report,
             "audit_gst_register": register_audit.audit_gst_register, "tds_check": tds_check.tds_check,
             "open_spreadsheet": sheet_tools.open_spreadsheet,
             "edit_spreadsheet": sheet_tools.edit_spreadsheet, "query_spreadsheet": sheet_tools.query_spreadsheet,
             "ask_owner": extras.ask_owner}


def _spawn(app, coro) -> None:
    task = asyncio.create_task(coro)
    app.state.bg.add(task)
    task.add_done_callback(app.state.bg.discard)


def _live(app) -> Counter:
    """Runs working in this process right now, per session. An owner message joins only these. A RUNNING row left
    behind by a restart has no run behind it, so a message attached to it would never be read. A count, not a flag:
    a finishing run and the continuation that starts on the same session can overlap, and each must hold its own mark."""
    if not hasattr(app.state, "live_runs"):
        app.state.live_runs = Counter()
    return app.state.live_runs


def _mark(app, sid: str, on: bool) -> None:
    counts = _live(app)
    if on:
        counts[sid] += 1
    else:
        counts[sid] -= 1
        if counts[sid] <= 0:
            del counts[sid]


async def attach_to_running(app, tenant_id: str, content: str, files=()) -> bool:
    """An owner message while a run is working on a task joins that run: it is read at the run's next step, and no
    second run starts. False when no run is live for this shop; the caller then starts a session as a normal message."""
    live = list(_live(app))
    if not live:
        return False
    item = json.dumps([{"content": content, "files": list(files)}])
    async with app.state.pool.connection() as conn:
        row = await (await conn.execute(
            """UPDATE agent_sessions SET inbox = inbox || %s::jsonb
                WHERE id = (SELECT id FROM agent_sessions WHERE status = 'RUNNING' AND trust = 'TRUSTED'
                            AND tenant_id = %s AND id::text = ANY(%s::text[]) ORDER BY created_at DESC LIMIT 1)
                  AND status = 'RUNNING'
                RETURNING id""", (item, tenant_id, live))).fetchone()
    return row is not None


async def _drain(pool, sid: str) -> list[dict]:
    """Take and clear the owner messages waiting for a session. Their files become part of that session."""
    async with pool.connection() as conn:
        row = await (await conn.execute("SELECT inbox FROM agent_sessions WHERE id = %s FOR UPDATE", (sid,))).fetchone()
        items = (row or {}).get("inbox") or []
        if items:
            files = [f for it in items for f in it.get("files", [])]
            await conn.execute("UPDATE agent_sessions SET inbox = '[]'::jsonb, files = files || %s::text[] WHERE id = %s",
                               (files, sid))
    return items


async def _take_inbox(app, row, messages: list) -> None:
    """Put the owner messages that arrived during the last step into the conversation, where the model reads them next."""
    for it in await _drain(app.state.pool, str(row["id"])):
        messages.append({"role": "user", "content": f"[sent while you were working]\n{it['content']}"})
        row["files"] = [*row["files"], *it.get("files", [])]


async def _finish_unless_inbox(pool, sid: str, messages: list, steps: int) -> bool:
    """Mark the run DONE, unless an owner message arrived during the last step. Returns False then: the run goes on."""
    async with pool.connection() as conn:
        row = await (await conn.execute(
            """UPDATE agent_sessions SET status = 'DONE', messages = %s::jsonb, steps = %s, updated_at = now()
                WHERE id = %s AND jsonb_array_length(inbox) = 0 RETURNING id""",
            (json.dumps(messages, default=str), steps, sid))).fetchone()
    return row is not None


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


def fill_place(messages: list[dict]) -> tuple[str, str]:
    """(the box, the page's address) of the agent's most recent 'fill', as noted in that step's result."""
    for m in reversed(messages):
        if m.get("role") == "tool" and str(m.get("content") or "").startswith('{"typed_into": '):
            try:
                note, _ = json.JSONDecoder().raw_decode(m["content"], len('{"typed_into": '))
                return str(note["box"]), str(note["url"])
            except (ValueError, KeyError, TypeError):
                break
    return "", ""


def _note_fill(args: dict, ctx, result: dict, browsers) -> dict:
    """First in the result, so cutting a long page never removes it."""
    if args.get("action") != "fill" or result.get("error"):
        return result
    box = control(ctx.controls, args["ref"]) if args.get("ref") not in (None, "") else str(args.get("selector") or "")
    url = str((browsers.page(ctx.id) or {}).get("url", "")) if browsers else ""
    return {"typed_into": {"box": box[:120], "url": url}, **result}


def _ctx(row, call_id: str = "", browsers=None) -> SessionCtx:
    sid = str(row["id"])
    return SessionCtx(id=sid, tenant_id=str(row["tenant_id"]), trust=row["trust"],
                      party_id=str(row["party_id"]) if row["party_id"] else None, files=tuple(row["files"]), call_id=call_id,
                      grants=active_grants(row), host=host_of(browsers.page(sid)) if browsers else "",
                      on_device=bool(browsers) and getattr(browsers, "where", lambda _: "")(sid) == "device",
                      page=page_text(browsers.page(sid)) if browsers else "",
                      controls=str((browsers.page(sid) or {}).get("page", "")) if browsers else "",
                      last_fill=last_fill(row.get("messages") or [])[0], last_fill_value=last_fill(row.get("messages") or [])[1],
                      last_fill_box=fill_place(row.get("messages") or [])[0], last_fill_url=fill_place(row.get("messages") or [])[1],
                      url=str((browsers.page(sid) or {}).get("url", "")) if browsers else "",
                      owner_words=owner_words(row.get("messages") or []), confirmed=answered_question(row.get("messages") or []))


def answered_question(messages: list[dict]) -> bool:
    """True when the owner's latest message is the answer to an ask_owner question (the first thing they said since it)."""
    def real(m):
        return m.get("role") == "user" and not str(m.get("content") or "").startswith(("[system note]", "[Earlier conversation omitted"))
    asked = [i for i, m in enumerate(messages) if m.get("role") == "assistant"
             and any(c.get("function", {}).get("name") == "ask_owner" for c in (m.get("tool_calls") or []))]
    if not asked:
        return False
    return sum(1 for m in messages[asked[-1] + 1:] if real(m)) == 1


def owner_words(messages: list[dict], last: int = 12) -> str:
    """The owner's own recent messages (not system notes), newest last."""
    said = [m.get("content") or "" for m in messages if m.get("role") == "user"]
    said = [t for t in said if not t.startswith(("[system note]", "[Earlier conversation omitted"))]
    return "\n".join(said[-last:])[-4000:]


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
                        channel: str = "whatsapp", background: bool = True, fallback=None,
                        message_ref: str | None = None) -> str:
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
    if message_ref:                     # the owner message that started this run: the run holds it, so the log must not repeat it
        await chatlog.attach(pool, tenant.id, message_ref, sid)
    _mark(app, sid, True)               # from now on an owner message joins this run (attach_to_running); run() unmarks
    started = False
    try:
        job = run(app, sid, fallback=fallback)
        started = True
        _spawn(app, job) if background else await job
    except BaseException:
        if not started:
            _mark(app, sid, False)
        raise
    return sid


HEAVY_TOOLS = frozenset({"run_python", "inspect_and_import_file", "browser_step", "read_attachment", "make_document",
                         "open_spreadsheet", "edit_spreadsheet", "query_spreadsheet"})


def heavy(messages: list[dict]) -> bool:
    """File, spreadsheet and browser work needs the strong model start to finish; only light chat may use the fallback."""
    used = any(c["function"]["name"] in HEAVY_TOOLS for m in messages for c in (m.get("tool_calls") or []))
    attached = any("file_ref=" in str(m.get("content", "")) for m in messages if m["role"] == "user")
    asked = any(_FILE_ASK.search(str(m.get("content", ""))) for m in messages[-6:] if m["role"] == "user")
    return used or attached or asked


_FILE_ASK = re.compile(r"\b(pdf|excel|xlsx)\b", re.I)
_SEND_ASK = re.compile(r"(bhej|send|bana|de do|dedo|chahiye|chahie|mail)", re.I)


# A job handed to the owner's PC must not also be done here: the owner gets two results, and ours was made without reading
# their files. These are the tools that would do or deliver it. A new owner message lifts the block.
PC_DOES_IT = frozenset({"run_python", "make_document", "edit_spreadsheet", "send_file_to_owner", "browser_step"})
ON_THE_PC = ("This job is on the owner's PC now; do not do or send it here. Reply in one or two lines that their PC is working on it "
             "and the result will come in this chat. If they also asked for something separate, say you will do it when they send "
             "it as its own message.")


def handed_to_pc(messages: list[dict]) -> bool:
    """True when run_on_pc succeeded since the owner's latest message."""
    last = max((i for i, m in enumerate(messages) if m["role"] == "user"
                and not str(m.get("content") or "").startswith(("[system note]", "[Earlier conversation omitted"))), default=-1)
    turn = messages[last + 1:]
    ids = {c["id"] for m in turn for c in (m.get("tool_calls") or []) if c["function"]["name"] == "run_on_pc"}
    return any(m["role"] == "tool" and m.get("tool_call_id") in ids and '"task_id"' in (m.get("content") or "") for m in turn)


def asked_owner(messages: list[dict], calls: list[dict]) -> bool:
    """True when this step put a question to the owner (ask_owner went out)."""
    ids = {c["id"] for c in calls if c["function"]["name"] == "ask_owner"}
    return any(m["role"] == "tool" and m.get("tool_call_id") in ids and '"asked": true' in (m.get("content") or "")
               for m in messages[-len(calls) - 2:])


def owes_file(messages: list[dict]) -> bool:
    """True when the owner's latest message asked for a PDF/Excel file and this turn never sent one."""
    if handed_to_pc(messages):
        return False      # the PC makes it
    last = max((i for i, m in enumerate(messages) if m["role"] == "user" and not m["content"].startswith("[system note]")), default=-1)
    if last < 0 or not (_FILE_ASK.search(messages[last]["content"]) and _SEND_ASK.search(messages[last]["content"])):
        return False
    return not any(m["role"] == "tool" and (('"sent": true' in m["content"] and '"filename"' in m["content"]) or '"files_sent_to_owner": ["' in m["content"])
                   for m in messages[last:])        # gst_reconcile / bank_reconcile / ageing_report send their own files


_CLAIM_REMINDER = re.compile(r"(yaad\s*dila\s*(dunga|dungi|doonga|doongi|ungi|unga)|reminder\s*(set|laga\s*(diya|di)|ho\s*gaya)|set\s*kar\s*(diya|di)|"
                             r"schedule\s*kar\s*(diya|di)|i['’]ll remind|i will remind|reminder is set|याद\s*दिला\s*(दूंगी|दूंगा))", re.I)
_CLAIM_FILE = re.compile(r"((file|excel|pdf|report|statement|sheet|document|attachment|brs)\W+(\w+\W+){0,4}(bhej\s*(diya|di\b|di\s*hai)|bheji\b|sent\b|attached\b)|"
                         r"(bhej\s*(diya|di\b|di\s*hai)|sent|attached)\W+(\w+\W+){0,3}(file|excel|pdf|report|statement|sheet|document))", re.I)


# "Task bhej diya hai" / "sent to your laptop" is about the job, not a file. A file word before the verb keeps it a file claim.
_JOB_SENT = re.compile(r"\b(?:task|kaam|job|request)\b(?:(?!\b(?:pdf|file|excel|report|statement|sheet|document|attachment|brs)\b)[^.!?\n]){0,40}?"
                       r"\b(?:bhej\s*(?:diya|di)(?:\s*hai)?|sent)\b"
                       r"|\b(?:laptop|pc)\s+(?:pe|par|ko)\s+bhej\s*(?:diya|di)(?:\s*hai)?\b"
                       r"|\bsent\s+(?:(?:the|your|this|that)\s+)?(?:task|job|request)\b"
                       r"|\bsent\s+to\s+(?:(?:your|the)\s+)?(?:laptop|pc)\b", re.I)


_ASKS_AMOUNT = re.compile(r"(kitna|kitne|kitni|how much|total kya|kya total|amount kya|kya amount|dena hai|dena padega|payable)", re.I)
_MONEYISH = re.compile(r"(?:rs\.?|₹|inr|rupees?|rupaye)\s*[\d,]+|\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d{3,}", re.I)


def gives_a_figure(reply: str) -> bool:
    """An amount-like number in the reply (Rs 50, 8,803, 12000); a year or a day and month does not count."""
    return bool(_MONEYISH.search(re.sub(r"\b(?:19|20)\d\d\b", " ", reply or "")))


_STANDING_WORDS = re.compile(r"(yaad\s*rakh|aage\s*se|jab\s*bhi|jab\s*kabhi|har\s*baar|hamesha|always|whenever|every\s*time|from now on|\brule\b)", re.I)


_NEGATION = re.compile(r"\b(?:no|not|none|never|nothing|nahi|nahin|nhi)\b|n['’]t\b|नहीं|नही\b", re.I)


def claims(pattern: re.Pattern, reply: str) -> bool:
    """True when the reply says it was done. A match with a 'no' or 'not' right around it, in the same sentence, says it was
    not ('koi reminder set nahi hai', 'no file was sent') and must not be corrected as a false claim."""
    for m in pattern.finditer(reply):
        before = re.split(r"[.!?\n।]", reply[:m.start()])[-1][-20:]
        after = re.split(r"[.!?\n।,]", reply[m.end():])[0][:14]
        if not _NEGATION.search(f"{before} {m.group(0)} {after}"):
            return True
    return False


def unbacked_claim(messages: list[dict], reply: str) -> str:
    """A system note when the reply says something was done that this turn never did: a reminder 'set' with no schedule_followup, a file
    'sent' with no successful send. '' when the claim is backed (or the reply is a question). The owner would otherwise wait for a
    reminder that never comes or a file that never arrived."""
    reply = reply or ""
    if "?" in reply or re.search(r"(?m)^\s*1[.)]\s", reply):
        return ""
    last = max((i for i, m in enumerate(messages) if m["role"] == "user" and not m["content"].startswith("[system note]")), default=-1)
    turn = messages[last + 1:] if last >= 0 else messages
    calls = [c["function"]["name"] for m in turn for c in (m.get("tool_calls") or [])]
    taught = "write_continuum_evidence" in calls and bool(_STANDING_WORDS.search(str(messages[last].get("content") or ""))) if last >= 0 else False
    listing = {"list_followups", "cancel_followup"} & set(calls) and last >= 0 and _LIST_ASK.search(str(messages[last].get("content") or ""))
    if claims(_CLAIM_REMINDER, reply) and "schedule_followup" not in calls and not taught and not listing:
        # (a standing rule saved to memory is not a reminder set now, and nor is reporting the ones already there)
        return ("[system note] You said a reminder is set, but you did not call schedule_followup in this turn, so nothing is scheduled and the owner "
                "would never be reminded. Call schedule_followup now with the exact date and time. Then write your COMPLETE answer to the owner again: "
                "everything they asked in this message (the figures, dates and totals) first, and the time the reminder will come in one line at the end. "
                "If you could not set it, say so plainly instead.")
    sent = any(m["role"] == "tool" and ('"sent": true' in (m.get("content") or "") or '"files_sent_to_owner": ["' in (m.get("content") or "")) for m in turn)
    if not sent and handed_to_pc(messages):
        if not claims(_CLAIM_FILE, _JOB_SENT.sub(" . ", reply)):
            return ""
        return ("[system note] You said a file was sent, but nothing was sent from here: the job is on the owner's PC and is not "
                "finished. Tell them that, and that the result will come in this chat. Do not make or send a file yourself.")
    if claims(_CLAIM_FILE, reply) and not sent:
        return ("[system note] You said you sent a file, but no send succeeded in this turn, so the owner has nothing. Send it now with "
                "send_file_to_owner, or tell them plainly that it could not be sent.")
    return ""


def unanswered_amount(messages: list[dict], reply: str) -> str:
    """A system note when the owner asked for an amount in this message, something was looked up this turn, and the reply has no amount in it
    (a bill plus 'kitna dena hai?' answered with only 'reminder set'). Its own nudge, so it still works after a reminder or file claim was corrected."""
    reply = reply or ""
    if "?" in reply or re.search(r"(?m)^\s*1[.)]\s", reply) or handed_to_pc(messages):
        return ""         # after a hand-off the figure comes from the PC; asking for it here would invite a guess
    last = max((i for i, m in enumerate(messages) if m["role"] == "user" and not m["content"].startswith("[system note]")), default=-1)
    if last < 0 or not any(c for m in messages[last + 1:] for c in (m.get("tool_calls") or [])):
        return ""
    if _ASKS_AMOUNT.search(str(messages[last].get("content") or "")) and not gives_a_figure(reply):
        return ("[system note] The owner asked for an amount in this message, and your reply has no amount in it. Give them the figure now, from the file "
                "or the tool results you already have (say which amount it is), then add whatever you did for them in one line. If you truly cannot "
                "find it, say that plainly and ask.")
    return ""


_LIST_ASK = re.compile(r"(dikha|dikhao|dikhana|\blist\b|\bshow\b|kaunse|kaun\s*se|kitne|hata\b|hata\s*do|cancel|delete|band\s*kar|lage\s*hain|pending\s*hain)", re.I)

# One tool does a whole piece of accountant's work and says so in its result. After it has run, redoing the same two files with
# hand-written code (and getting other figures) is the failure to prevent: the first such attempt is turned back with the reason.
SPECIALISTS = {"gst_reconcile": ("books_file_ref", "gstr2b_file_ref"), "bank_reconcile": ("book_file_ref", "bank_file_ref")}


def specialist_redirect(messages: list[dict], name: str, args) -> dict | None:
    if name != "run_python" or not isinstance(args, dict) or any('"redirected": true' in (m.get("content") or "") for m in messages if m["role"] == "tool"):
        return None
    refs = {r for r in (args.get("file_refs") or []) if isinstance(r, str)}
    done = {m.get("tool_call_id"): m.get("content") or "" for m in messages if m["role"] == "tool"}
    for m in messages:
        for c in m.get("tool_calls") or []:
            tool = c["function"]["name"]
            if tool not in SPECIALISTS:
                continue
            try:
                a = json.loads(c["function"].get("arguments") or "{}")
            except ValueError:
                continue
            pair = {a.get(k) for k in SPECIALISTS[tool]}
            if len(pair) == 2 and pair <= refs and not done.get(c["id"], '{"error"').startswith('{"error"'):
                return {"error": f"{tool} already did this for these two files: its result holds every figure, and it makes the Excel. Call {tool} "
                                 f"again (it takes seconds) and quote its summary_lines; do not match or total these files with your own code. "
                                 f"If you truly need something it does not give, send this run_python call again and it will run.", "redirected": True}
    return None


_REMIND_ASK = re.compile(r"(remind(?!\s+me)\w*\s+\w+|reminder.{0,30}(bhej|send|kar|de\b|do\b)|(bhej|send).{0,30}(reminder|msg|message)|"
                         r"payment.{0,20}(maang|bhej)|रिमाइंडर.{0,30}(भेज|कर|दो)|"
                         r"भेज.{0,30}(रिमाइंडर|मैसेज))", re.I)
_QUESTION = re.compile(r"\?|\b(kaun|kaunsa|kisko|kiska|kis\b|who|which|what|kya)\b|कौन|किसको|क्या", re.I)   # "kisko chase karun?" asks; it does not order
_NOT_POSSIBLE = re.compile(r"((phone|number|mobile).{0,40}(nahi|not |missing|no |nhi)|cannot|can't|can not|nahi bhej sakta|not in the ledger|"
                           r"ledger mein nahi|no customer|koi customer nahi|kaunsa|which one)", re.I)


def _sent_to_customer(call: dict) -> bool:
    if call["function"]["name"] != "send_whatsapp_message":
        return False
    try:
        return json.loads(call["function"].get("arguments") or "{}").get("to") != "owner"
    except ValueError:
        return False


def owes_card(messages: list[dict], reply: str) -> bool:
    """True when the owner asked for a reminder/chase and this turn never made the send call to the CUSTOMER that creates the
    approval card (a draft sent to the owner as plain text, or a reply asking "shall I send?", is not one), and the reply does
    not give a reason it cannot be done. Nothing waits for the owner in that case."""
    last = max((i for i, m in enumerate(messages) if m["role"] == "user" and not m["content"].startswith("[system note]")), default=-1)
    ask = messages[last]["content"] if last >= 0 else ""
    if handed_to_pc(messages):
        return False      # "send it from my laptop" is the PC's job, with its own approval
    if not _REMIND_ASK.search(ask) or _QUESTION.search(ask) or _LIST_ASK.search(ask) or _NOT_POSSIBLE.search(reply or ""):
        return False
    return not any(_sent_to_customer(c) for m in messages[last:] for c in (m.get("tool_calls") or []))


async def run(app, sid: str, fallback=None) -> None:
    """One run of a session (see _run). The caller marked the session live (start_session, resume); this unmarks it."""
    try:
        await _run(app, sid, fallback=fallback)
    finally:
        _mark(app, sid, False)


async def _progress_notes(app, tenant) -> tuple[str, str]:
    pool = getattr(getattr(app, "state", None), "pool", None)
    if pool is None:
        return PROGRESS_NOTES
    try:
        language = (await prefs.get(pool, str(tenant.id)))["language"]
    except Exception:
        log.warning("language setting not read for a progress note")
        return PROGRESS_NOTES
    return PROGRESS_NOTES_IN.get(language, PROGRESS_NOTES)


async def _progress(app, row, tenant) -> None:
    """Tells the owner a long task is still going. Only for messages the owner sent, on the channel they used."""
    try:
        tg = getattr(getattr(app, "state", None), "tg", None)
        if row["channel"] == "telegram" and tg is not None and getattr(tenant, "telegram_chat_id", None):
            waited = 0.0                  # 'typing…' in the owner's chat while the answer is being worked out (free, no tokens)
            while waited < PROGRESS_FIRST_S:
                try:
                    await tg.typing(tenant.telegram_chat_id)
                except Exception:
                    log.info("typing indicator not shown")
                await asyncio.sleep(TYPING_EVERY_S)
                waited += TYPING_EVERY_S
        else:
            await asyncio.sleep(PROGRESS_FIRST_S)
        notes = await _progress_notes(app, tenant)
        for i in range(PROGRESS_MAX):
            await send_to_owner(app, tenant, text_message(tenant.owner_phone, notes[min(i, len(notes) - 1)]),
                                kind="owner_text", reply_channel=row["channel"])
            await asyncio.sleep(PROGRESS_EVERY_S)
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("progress note failed")


async def _run(app, sid: str, fallback=None) -> None:
    pool = app.state.pool
    async with pool.connection() as conn:
        row = await (await conn.execute("SELECT * FROM agent_sessions WHERE id = %s AND status = 'RUNNING'", (sid,))).fetchone()
    if row is None:
        return
    messages, steps, suspended, empties = row["messages"], row["steps"], False, 0
    budget, seen, nudged, card_nudged, claim_nudged, amount_nudged, busy = 0, {}, False, False, False, False, 0
    language_nudged = False
    ticker = None
    try:
        tenant = await tenants.by_id(pool, str(row["tenant_id"]))
        chatlog.CURRENT_SESSION_ID.set(sid)                # what Smara sends during this run is logged against it
        if row["trigger"] == "owner_message":
            CURRENT_REPLY_CHANNEL.set(row["channel"])      # the sends in this run answer on the owner's channel
            ticker = asyncio.create_task(_progress(app, row, tenant))
        if messages and messages[0]["role"] == "system":      # never carry a stale date/ledger/instructions into a new turn
            messages[0] = {"role": "system", "content": await build_system(app, tenant, row["trust"], str(row["id"]),
                                                                          query=latest_owner_text(messages))}
        while budget < MAX_STEPS:
            await _take_inbox(app, row, messages)          # owner messages sent during the last step are read now
            messages = compact(messages)
            try:
                msg = await app.state.llm.chat(messages, tools.TOOLS, allow_fallback=not heavy(messages))
            except (RateLimited, httpx.TransportError, httpx.HTTPStatusError) as err:
                if isinstance(err, httpx.HTTPStatusError) and err.response.status_code < 500:
                    raise                   # a 4xx other than a rate limit is a real error (bad request, bad key), not a hiccup
                busy += 1                   # a busy or briefly unreachable model is not a failed job: tell the owner once, wait, try the same step again
                if busy > RATE_RETRIES:
                    raise
                if busy == 1 and row["trust"] == "TRUSTED":
                    await _finish(app, row, "Model abhi busy hai, main thodi der mein dobara try karti hoon. Aapko kuch nahi karna.")
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
            if not calls and not claim_nudged and row["trust"] == "TRUSTED" and (note := unbacked_claim(messages[:-1], msg.get("content") or "")):
                claim_nudged = True    # self-check: it says a reminder is set or a file sent that this turn never made
                messages.append({"role": "user", "content": note})
                continue
            if not calls and not amount_nudged and row["trust"] == "TRUSTED" and (note := unanswered_amount(messages[:-1], msg.get("content") or "")):
                amount_nudged = True   # self-check: the owner asked for an amount and the reply has none
                messages.append({"role": "user", "content": note})
                continue
            if not calls and not card_nudged and row["trust"] == "TRUSTED" and owes_card(messages[:-1], msg.get("content") or ""):
                card_nudged = True     # self-check: it described a reminder but made no card, so the owner would wait for nothing
                messages.append({"role": "user", "content": "[system note] You said a reminder is ready or asked whether to send it, "
                                 "but you did not call send_whatsapp_message to the CUSTOMER, so the owner has no approval card (a draft sent to the owner "
                                 "is not one). Call send_whatsapp_message now with to set to the customer and the exact message (the owner's tap sends it). "
                                 "If it cannot be sent (no phone number, unknown customer), say that plainly instead."})
                continue
            if not calls and not language_nudged and row["trust"] == "TRUSTED" and \
                    (note := prefs.wrong_language((await prefs.get(pool, str(row["tenant_id"])))["language"], msg.get("content") or "")):
                language_nudged = True     # self-check: they chose a language and the reply is in another one
                messages.append({"role": "user", "content": note})
                continue
            if not calls:
                if not await _finish_unless_inbox(pool, sid, messages, steps):
                    await _save(pool, sid, messages, steps=steps)     # an owner message came in during this step: read it next
                    await _finish(app, row, msg.get("content") or "")  # what it said so far still reaches the owner
                    continue
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
            if asked_owner(messages, calls):       # a question is with the owner: the turn ends; the answer is their next message
                if await _finish_unless_inbox(pool, sid, messages, steps):
                    return
        # Out of budget: never go silent. Ask the model for an honest status instead of a dead end.
        await _take_inbox(app, row, messages)              # what the owner sent meanwhile is in the status it writes
        messages.append({"role": "user", "content": "[system note] Step budget reached. Without calling any tool, tell the owner "
                         "briefly what you finished, what is left, and what you need from them to continue."})
        summary = ((await app.state.llm.chat(messages, [])).get("content") or "").strip()
        messages.append({"role": "assistant", "content": summary})
        await _save(pool, sid, messages, status="DONE", steps=steps)
        await _finish(app, row, summary or "This took more steps than I can do in one go. Tell me to continue and I will pick it up.")
    except Exception:
        log.exception("agent session %s failed", sid)
        await _save(pool, sid, messages, status="FAILED", steps=steps)
        unread = await _drain(pool, sid)          # owner messages that arrived and were never read: say so, never silence
        if fallback and steps <= 1:
            await fallback()   # e.g. the deterministic debtor path, so a claim is never lost
        elif row["trust"] == "TRUSTED":
            text = "Sorry, I could not finish that right now. Please try again in a minute."
            if unread:
                text += " Your message sent while I was working was not read; please send it again."
            try:
                await _finish(app, row, text)
            except Exception:   # the channel itself may be down: log it, do not crash the run task
                log.exception("could not tell the owner that session %s failed", sid)
    finally:
        if ticker is not None:
            ticker.cancel()
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
        if name in PC_DOES_IT and handed_to_pc(messages):
            result = {"error": ON_THE_PC}
        elif d.verdict == "allow" and (turned_back := specialist_redirect(messages, name, args)):
            result = turned_back
        elif d.verdict == "deny":
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
                page = getattr(getattr(app.state, "browsers", None), "page", lambda _: None)(ctx.id)
                typed = ctx.last_fill_value if name == "browser_step" and typed_into_form(ctx) else ""
                language = (await prefs.get(app.state.pool, ctx.tenant_id))["language"] if name == "browser_step" else ""
                extra = await actions.open_gate(app, ctx, tenant, name, args,   # args now hold computed amounts
                                                approval_text(name, args, page, typed, language), language)
                pending = {"call_id": cid, "name": name, "args": args, "card": extra.get("card", d.card),
                           "remaining": calls[i + 1:], **{k: v for k, v in extra.items() if k != "card"}}
                await _save(app.state.pool, ctx.id, messages, status="WAITING_APPROVAL", pending=pending)
                return True
            result = {"error": err}
        if name == "web_search" and (advice := search_advice(messages)):
            result = {"advice": advice, **result}        # first, so the cut on long results never removes it
        if name == "browser_step":
            result = _note_fill(args, ctx, result, getattr(app.state, "browsers", None))
        messages.append({"role": "tool", "tool_call_id": cid,
                         "content": tools.bounded(result, BROWSER_RESULT_CHARS if name == "browser_step" else 6000)})
    return False


def search_advice(messages: list[dict]) -> str:
    """When a request has had many searches, a push to answer: weak results otherwise keep the model searching for minutes.
    Advice only; the search has already run and further ones are still allowed."""
    last = max((i for i, m in enumerate(messages) if m["role"] == "user"
                and not str(m.get("content") or "").startswith(("[system note]", "[Earlier conversation omitted"))), default=-1)
    n = sum(1 for m in messages[last + 1:] for c in (m.get("tool_calls") or []) if c["function"]["name"] == "web_search")
    if n >= SEARCH_PLENTY:
        return (f"{n} searches for this one request. Stop searching and answer now: what you found with its source, and plainly "
                "what you could not confirm.")
    if n >= SEARCH_ENOUGH:
        return (f"{n} searches for this one request. If anything you have is usable, answer now with its source and say plainly "
                "what you could not confirm. Search again only if you have nothing at all.")
    return ""


async def _execute(app, ctx, tenant, name, args) -> dict:
    try:
        if name == "send_whatsapp_message":   # T1: to the owner only (T2 to customers is gated)
            text = text_message(tenant.owner_phone, str(args.get("text", ""))[:12000])
            # The owner just wrote: reply at once. A scheduled job messaging them unprompted is a held-able note.
            kind = "owner_note" if ctx.trust == "SYSTEM" else "owner_text"
            via = await send_to_owner(app, tenant, text, kind=kind)
            return {"sent": via != "held", "to": "owner", "queued_until_owner_reachable": via == "held"}
        if name == "browser_step":
            return await app.state.browsers.step(ctx.id, args, tenant_id=ctx.tenant_id)
        return await EXECUTORS[name](app, ctx, args)
    except Exception as e:
        health_watch.tool_failed(name, e)            # counted for the hourly ops report (no tokens, no model)
        log.warning("tool %s raised %s", name, type(e).__name__)
        return {"error": f"{type(e).__name__}: {str(e)[:300]}"}


REDELIVER_AFTER_S = (15, 45, 120, 300, 600)       # when the channel is down: try the answer again after each of these


def _channel_down(err: Exception) -> bool:
    """A network problem or a 5xx/429 from Telegram or WhatsApp: the answer is fine, the channel is not. Worth retrying."""
    if isinstance(err, httpx.HTTPStatusError):
        return err.response.status_code >= 500 or err.response.status_code == 429
    return isinstance(err, httpx.HTTPError)


async def _finish(app, row, text: str) -> None:
    if row["trust"] == "TRUSTED" and text.strip():
        tenant = await tenants.by_id(app.state.pool, str(row["tenant_id"]))
        payload = text_message(tenant.owner_phone, text[:12000])
        try:
            await send_to_owner(app, tenant, payload, kind="owner_text", reply_channel=row["channel"])
        except Exception as err:
            if not _channel_down(err):
                raise
            # The answer exists but Telegram did not take it (seen 16 Nov: every call to Telegram timed out for a while, the
            # run was marked FAILED and the owner was left at 'Still working on it'). Keep trying in the background.
            log.warning("could not deliver the answer (%s); will retry", type(err).__name__)
            _spawn(app, _redeliver(app, tenant, payload, row["channel"], str(row["id"])))


async def _redeliver(app, tenant, payload: dict, channel: str | None, sid: str) -> None:
    for wait in REDELIVER_AFTER_S:
        await asyncio.sleep(wait)
        try:
            await send_to_owner(app, tenant, payload, kind="owner_text", reply_channel=channel)
            log.info("answer for session %s delivered after a retry", sid)
            return
        except Exception as err:
            if not _channel_down(err):
                break
    log.error("answer for session %s could not be delivered after %d retries", sid, len(REDELIVER_AFTER_S))


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
    p, messages, sid = row["pending"], row["messages"], str(row["id"])
    _mark(app, sid, True)               # from now until the run has the session, an owner message joins it
    started = False
    try:
        ctx, tenant = _ctx(row, p["call_id"], getattr(app.state, "browsers", None)), await tenants.by_id(app.state.pool, tenant_id)
        if outcome is not None:          # propose_ledger_adjustment: the ledger path already applied the decision
            result = outcome
        elif approved and pre_tool_use(ctx, p["name"], p["args"], tools.TOOL_NAMES).verdict in ("approve", "allow"):
            result = await actions.execute_approved(app, ctx, tenant, p["name"], p["args"])
        else:
            await actions.on_rejected(app, ctx, p["name"], p["args"])
            result = {"error": "the owner rejected this action"}
        if p["name"] == "browser_step" and approved:
            result = _note_fill(p["args"], ctx, result, getattr(app.state, "browsers", None))
        messages.append({"role": "tool", "tool_call_id": p["call_id"], "content": tools.bounded(result)})
        if await _process(app, row, messages, p.get("remaining") or []):
            _mark(app, sid, False)      # waiting on the owner again: not running
            return True
        await _save(app.state.pool, ctx.id, messages, pending=None)
        job = run(app, ctx.id)          # run() unmarks when it ends
        started = True
        _spawn(app, job) if background else await job
        return True
    except BaseException:
        if not started:
            _mark(app, sid, False)
        raise
