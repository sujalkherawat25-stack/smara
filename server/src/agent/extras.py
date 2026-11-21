"""Executors for time, scheduling, GSTIN, payment requests and files to the owner."""
import json
import mimetypes
import re
from datetime import datetime, timedelta

from src import clock
from src.agent import actions
from src.agent.policy import REACTIONS, SessionCtx
from src.box.gstin import check_gstin_status
from src.loop.owner_channel import send_document_to_owner
from src.loop.upi import pay_url, rupees, upi_text

MAX_PENDING = 25
MAX_PER_DAY = 50
MAX_AHEAD = timedelta(days=400)     # a GST or advance-tax date next March is a normal thing to ask for
REPEATS = ("daily", "weekly", "monthly", "yearly")
MAX_SERIES = 12                    # reminders that come back by themselves, per shop
MIN_AHEAD = timedelta(minutes=1)
MAX_FILE_BYTES = 45_000_000      # Telegram bots send up to 50 MB (they only DOWNLOAD 20 MB)
MAX_RETRIES, RETRY_WINDOW = 2, timedelta(hours=6)     # quick re-schedules from a follow-up run, per 12 hours


async def _retries_used(conn, ctx: SessionCtx) -> int:
    """How many soon-running follow-ups were scheduled by follow-up runs (i.e. retries) in the last 12 hours. 0 if this
    session is not itself a follow-up run (the owner asking for reminders is never limited by this)."""
    me = await (await conn.execute("SELECT trigger FROM agent_sessions WHERE id::text = %s", (ctx.id,))).fetchone()
    if not me or me["trigger"] != "scheduled_followup":
        return 0
    row = await (await conn.execute(
        """SELECT count(*) n FROM agent_tasks t JOIN agent_sessions s ON s.id = t.created_by_session
            WHERE t.tenant_id = %s AND t.kind = 'followup' AND s.trigger = 'scheduled_followup'
              AND t.created_at > now() - interval '12 hours' AND t.run_at < t.created_at + interval '6 hours'""",
        (ctx.tenant_id,))).fetchone()
    return row["n"]
SENDABLE = {".pdf": "application/pdf", ".csv": "text/csv", ".txt": "text/plain",
            ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


async def get_current_time(app, ctx: SessionCtx, args: dict) -> dict:
    s, now = app.state.settings, clock.now()
    return {"now_ist": now.strftime("%Y-%m-%d %H:%M"), "weekday": now.strftime("%A"), "date": now.date().isoformat(),
            "readable": clock.describe(now), "business_hours_open": not clock.is_quiet(s, now),
            "quiet_hours_ist": f"{s.quiet_start_hour}:00-{s.quiet_end_hour}:00",
            "next_open_ist": clock.next_open(s, now).strftime("%Y-%m-%d %H:%M")}


def _when(args: dict, now: datetime) -> datetime | str:
    """Parse at_ist ('YYYY-MM-DD HH:MM' or ISO) / in_minutes into an aware IST datetime, or an error string."""
    if args.get("in_minutes") is not None:
        try:
            return now + timedelta(minutes=int(args["in_minutes"]))
        except (TypeError, ValueError):
            return "in_minutes must be a whole number"
    raw = str(args.get("at_ist") or "").strip()
    if not raw:
        return "give at_ist ('YYYY-MM-DD HH:MM', Indian time) or in_minutes"
    try:
        at = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return "at_ist must look like 2026-11-05 10:00"
    return at.replace(tzinfo=clock.IST) if at.tzinfo is None else at.astimezone(clock.IST)


def next_run(after: datetime, recur: dict, now: datetime | None = None) -> datetime | None:
    """The next time a repeating reminder is due, after `after` and after `now`; None once its last date has passed.
    Monthly keeps its day of the month (the 31st becomes the last day of a shorter month); yearly keeps month and day."""
    import calendar
    base = after.astimezone(clock.IST)
    now = (now or clock.now()).astimezone(clock.IST)
    every, day = recur.get("every"), int(recur.get("day") or base.day)
    until = recur.get("until")
    cur = base
    for _ in range(4000):
        if every == "daily":
            cur = cur + timedelta(days=1)
        elif every == "weekly":
            cur = cur + timedelta(days=7)
        elif every == "monthly":
            y, m = (cur.year + (cur.month == 12), 1 if cur.month == 12 else cur.month + 1)
            cur = cur.replace(year=y, month=m, day=min(day, calendar.monthrange(y, m)[1]))
        elif every == "yearly":
            y = cur.year + 1
            cur = cur.replace(year=y, day=min(day, calendar.monthrange(y, cur.month)[1]))
        else:
            return None
        if cur > now:
            break
    if until and cur.date().isoformat() > str(until):
        return None
    return cur


def _repeat(args: dict, at: datetime) -> dict | str | None:
    """The repeat rule from the tool arguments: None when the reminder is once, a dict, or an error string."""
    every = str(args.get("repeat") or "").strip().lower()
    if not every or every in ("none", "once", "no"):
        return None
    if every not in REPEATS:
        return f"repeat must be one of {', '.join(REPEATS)} (or leave it out for a one-time reminder)"
    until = str(args.get("until_ist") or "").strip()[:10]
    if until:
        try:
            if datetime.fromisoformat(until).date() < at.date():
                return "until_ist is before the first reminder"
        except ValueError:
            return "until_ist must look like 2027-03-31"
    return {"every": every, "day": at.day, **({"until": until} if until else {})}


async def _owner_words(app, session_id: str) -> str:
    """The owner's latest message in this conversation, verbatim. Stored with a follow-up so that if the model
    paraphrases or 'corrects' a name or a time, the original words still travel with the task."""
    try:
        async with app.state.pool.connection() as conn:
            row = await (await conn.execute("SELECT messages FROM agent_sessions WHERE id::text = %s", (session_id,))).fetchone()
    except Exception:
        return ""
    users = [m["content"] for m in (row["messages"] if row else []) if m["role"] == "user"
             and not m["content"].startswith("[Earlier conversation omitted")]
    return users[-1][:500] if users else ""


async def schedule_followup(app, ctx: SessionCtx, args: dict) -> dict:
    instruction = str(args.get("instruction") or "").strip()
    if not instruction or len(instruction) > 1000:
        return {"error": "instruction must be 1-1000 characters"}
    now = clock.now()
    at = _when(args, now)
    if isinstance(at, str):
        return {"error": at}
    if at < now + MIN_AHEAD or at > now + MAX_AHEAD:
        return {"error": "the first time must be between 1 minute and 400 days from now (check get_current_time)"}
    owner_said = await _owner_words(app, ctx.id)
    shifted = clock.is_quiet(app.state.settings, at)
    at = clock.next_open(app.state.settings, at)
    recur = _repeat(args, at)
    if isinstance(recur, str):
        return {"error": recur}
    async with app.state.pool.connection() as conn:
        c = await (await conn.execute(
            """SELECT count(*) FILTER (WHERE status = 'PENDING') pending,
                      count(*) FILTER (WHERE created_at > now() - interval '24 hours') today
                 FROM agent_tasks WHERE tenant_id = %s""", (ctx.tenant_id,))).fetchone()
        if c["pending"] >= MAX_PENDING or c["today"] >= MAX_PER_DAY:
            return {"error": f"too many scheduled tasks (max {MAX_PENDING} pending, {MAX_PER_DAY} per day); cancel some first"}
        if recur:
            series = await (await conn.execute("SELECT count(*) n FROM agent_tasks WHERE tenant_id = %s AND status = 'PENDING' AND payload ? 'recur'",
                                               (ctx.tenant_id,))).fetchone()
            if series["n"] >= MAX_SERIES:
                return {"error": f"already {MAX_SERIES} repeating reminders; cancel one first (list_followups shows them)"}
        if at < now + RETRY_WINDOW and await _retries_used(conn, ctx) >= MAX_RETRIES:
            # a scheduled job that keeps failing must not retry (and message the owner) every half hour all day
            return {"error": f"retry limit reached ({MAX_RETRIES} quick retries in 12 hours). Do not schedule another retry. "
                             "Tell the owner once, briefly, what failed and that they can ask again when it is fixed; "
                             "a next-day run (more than 6 hours ahead) is still allowed."}
        row = await (await conn.execute(
            """INSERT INTO agent_tasks (tenant_id, run_at, kind, payload, created_by_session)
               VALUES (%s, %s, 'followup', %s, %s) RETURNING id""",
            (ctx.tenant_id, at, json.dumps({"instruction": instruction, "owner_said": owner_said, **({"recur": recur} if recur else {})}), ctx.id))).fetchone()
    out = {"task_id": str(row["id"]), "will_run_at_ist": at.strftime("%Y-%m-%d %H:%M"), "moved_out_of_quiet_hours": shifted,
           "note": "When you confirm to the owner, use their exact words for any name; do not change names."}
    if recur:
        nxt = next_run(at, recur, at)
        out["repeats"] = recur["every"] + (f" until {recur['until']}" if recur.get("until") else " until cancelled")
        out["then_next"] = nxt.strftime("%Y-%m-%d %H:%M") if nxt else None
    return out


async def list_followups(app, ctx: SessionCtx, args: dict) -> dict:
    async with app.state.pool.connection() as conn:
        rows = await (await conn.execute(
            """SELECT id, run_at, kind, payload FROM agent_tasks
                WHERE tenant_id = %s AND status = 'PENDING' ORDER BY run_at LIMIT 30""", (ctx.tenant_id,))).fetchall()
    return {"pending": [{"task_id": str(r["id"]), "run_at_ist": r["run_at"].astimezone(clock.IST).strftime("%Y-%m-%d %H:%M"),
                         "kind": r["kind"], "what": (r["payload"].get("instruction") or r["payload"].get("text") or "")[:200],
                         **({"repeats": r["payload"]["recur"]["every"]} if r["payload"].get("recur") else {}),
                         "owner_said": r["payload"].get("owner_said", "")}
                        for r in rows]}


async def list_my_files(app, ctx: SessionCtx, args: dict) -> dict:
    """The files this person has sent over time (they are kept, so an earlier file can be opened again), newest first."""
    from datetime import datetime, timezone

    from src.ingest import owner_files
    rows = owner_files.agent_files(app.state.settings.owner_files_dir, ctx.tenant_id, datetime.now(timezone.utc))
    needle = str(args.get("name_contains") or "").strip().lower()
    if needle:
        rows = [r for r in rows if needle in r["name"].lower()]
    return {"files": [{"no": r["no"], "name": r["name"], "size_kb": round(r["bytes"] / 1024),
                       "kept_on": r["saved"].astimezone(clock.IST).strftime("%d %b %Y %H:%M"), "file_ref": r["file_ref"]}
                      for r in rows[:30]],
            "note": "To open files in run_python pass their file_ref values in file_refs; each then appears as ./in/<name> (without file_refs ./in is empty). "
                    "read_attachment takes the same file_ref. A file listed here still exists."}


async def cancel_followup(app, ctx: SessionCtx, args: dict) -> dict:
    try:
        async with app.state.pool.connection() as conn:
            row = await (await conn.execute(
                """UPDATE agent_tasks SET status = 'CANCELLED' WHERE id::text = %s AND tenant_id = %s
                      AND status = 'PENDING' RETURNING id""", (str(args.get("task_id")), ctx.tenant_id))).fetchone()
    except Exception:
        return {"error": "invalid task_id"}
    return {"cancelled": True} if row else {"error": "no pending task with that id"}


async def check_gstin(app, ctx: SessionCtx, args: dict) -> dict:
    return check_gstin_status(str(args.get("gstin", "")))


async def build_payment_request(app, ctx: SessionCtx, args: dict) -> dict:
    from src import tenants
    o = await actions._obligation(app.state.pool, ctx, args.get("obligation_id"))
    if not o:
        return {"error": "unknown obligation_id"}
    t = await tenants.by_id(app.state.pool, ctx.tenant_id)
    if not t or not t.upi:
        return {"error": "this shop has no UPI ID configured, so no payment request can be built"}
    text = upi_text(t.upi["vpa"], o["amount_paise"], o["invoice_ref"])
    link = pay_url(app.state.settings.public_base_url, t.upi["vpa"], t.upi.get("name", ""), o["amount_paise"],
                   o["invoice_ref"]) if app.state.settings.public_base_url else None
    return {"invoice": o["invoice_ref"], "amount_rupees": o["amount_paise"] / 100, "amount": rupees(o["amount_paise"]),
            "text": text + (f"\nTap to pay: {link}" if link else ""), "link": link}


async def react_to_owner(app, ctx: SessionCtx, args: dict) -> dict:
    """An emoji reaction on the owner's latest Telegram message. Never raises: a reaction is a nicety, so any failure is
    reported to the model as a short note and the turn carries on."""
    from src import tenants
    emoji = str(args.get("emoji") or "").strip()
    if emoji not in REACTIONS:
        return {"reacted": False, "error": "use one of: " + " ".join(REACTIONS)}
    tg = getattr(app.state, "tg", None)
    tenant = await tenants.by_id(app.state.pool, ctx.tenant_id)
    chat_id = getattr(tenant, "telegram_chat_id", None) if tenant else None
    mid = (getattr(app.state, "tg_last", None) or {}).get(chat_id)
    if tg is None or not chat_id or not mid:
        return {"reacted": False, "note": "no Telegram message to react to here; just write your reply"}
    try:
        await tg.react(chat_id, mid, emoji)
    except Exception as err:
        return {"reacted": False, "note": f"reaction not shown ({type(err).__name__}); just write your reply"}
    return {"reacted": True, "note": "the reaction is on their message; still write your normal reply"}


async def send_file_to_owner(app, ctx: SessionCtx, args: dict) -> dict:
    from src import tenants
    ref = str(args["file_ref"])
    _, job_id, name = (ref.split(":", 2) + ["", ""])[:3]
    ext = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in SENDABLE:
        return {"error": f"cannot send {ext or 'this'} file; allowed: {', '.join(sorted(SENDABLE))}"}
    data = app.state.sandbox.read_output(job_id, name)
    if not data or len(data) > MAX_FILE_BYTES:
        return {"error": "file is empty or larger than 45 MB"}
    from src.agent import sheet_tools
    if problems := await sheet_tools.edit_problems(app, ctx, data, name):     # however the copy was made
        return {"sent": False, "error": "not sent: this copy of the owner's sheet goes beyond what they asked: "
                + "; ".join(problems) + ". Redo it from their file with edit_spreadsheet, or ask_owner first."}
    if not getattr(ctx, "confirmed", False) and (gone := await sheet_tools.rows_removed(app, ctx, data, name)):
        return {"sent": False, "error": f"not sent: in this copy of the owner's sheet {gone}. Deleting their rows must be confirmed "
                                        "first: ask_owner what exactly should go (the rule you would use and how many rows it removes, "
                                        "with 2-3 examples), and send it only after they answer. If they only said something vague "
                                        "like 'bad rows', ask what makes a row bad."}
    tenant = await tenants.by_id(app.state.pool, ctx.tenant_id)
    mime = SENDABLE.get(ext) or mimetypes.guess_type(name)[0] or "application/octet-stream"
    caption = re.sub(r"\s+", " ", str(args.get("caption") or ""))[:900]
    via = await send_document_to_owner(app, tenant, data, name, mime, caption)
    if via == "window_closed":
        return {"sent": False, "error": "the owner's 24-hour WhatsApp window is closed, so a file cannot be sent now; "
                                        "tell them in your reply or try again after they message"}
    return {"sent": True, "via": via, "filename": name, "bytes": len(data)}


async def ask_owner(app, ctx: SessionCtx, args: dict) -> dict:
    """A question when the owner's words can mean more than one thing. Telegram shows the options as buttons (a tap sends
    the option as the owner's reply); WhatsApp gets them numbered. The turn ends here: the answer arrives as the owner's
    next message in this same conversation."""
    from src import tenants
    from src.loop.owner_channel import send_to_owner
    from src.loop.whatsapp import text_message
    q = " ".join(str(args.get("question") or "").split())
    opts = [" ".join(str(o).split())[:120] for o in (args.get("options") or []) if str(o).strip()]
    if not 5 <= len(q) <= 700:
        return {"error": "question: one short question (5 to 700 characters)"}
    if opts and not 2 <= len(opts) <= 4:
        return {"error": "options: 2 to 4 short answers the owner can tap, or none"}
    text = q + ("\n\n" + "\n".join(f"{i}. {o}" for i, o in enumerate(opts, 1)) if opts else "")
    tenant = await tenants.by_id(app.state.pool, ctx.tenant_id)
    payload = {**text_message(tenant.owner_phone, text), **({"_options": [f"{i}. {o}"[:120] for i, o in enumerate(opts, 1)]} if opts else {})}
    via = await send_to_owner(app, tenant, payload, kind="owner_text")
    return {"asked": True, "via": via, "note": "The question is with the owner. Your turn ends now; their answer comes as the "
                                               "next message (a tap sends e.g. '2. ...'). Do not write anything else now."}
