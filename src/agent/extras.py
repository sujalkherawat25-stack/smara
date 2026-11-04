"""Executors for time, scheduling, GSTIN, payment requests and files to the owner."""
import json
import mimetypes
import re
from datetime import datetime, timedelta

from src import clock
from src.agent import actions
from src.agent.policy import SessionCtx
from src.box.gstin import check_gstin_status
from src.loop.owner_channel import send_document_to_owner
from src.loop.upi import pay_url, rupees, upi_text

MAX_PENDING = 25
MAX_PER_DAY = 50
MAX_AHEAD = timedelta(days=60)
MIN_AHEAD = timedelta(minutes=1)
MAX_FILE_BYTES = 16_000_000
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


async def schedule_followup(app, ctx: SessionCtx, args: dict) -> dict:
    instruction = str(args.get("instruction") or "").strip()
    if not instruction or len(instruction) > 1000:
        return {"error": "instruction must be 1-1000 characters"}
    now = clock.now()
    at = _when(args, now)
    if isinstance(at, str):
        return {"error": at}
    if at < now + MIN_AHEAD or at > now + MAX_AHEAD:
        return {"error": "the time must be between 1 minute and 60 days from now (check get_current_time)"}
    shifted = clock.is_quiet(app.state.settings, at)
    at = clock.next_open(app.state.settings, at)
    async with app.state.pool.connection() as conn:
        c = await (await conn.execute(
            """SELECT count(*) FILTER (WHERE status = 'PENDING') pending,
                      count(*) FILTER (WHERE created_at > now() - interval '24 hours') today
                 FROM agent_tasks WHERE tenant_id = %s""", (ctx.tenant_id,))).fetchone()
        if c["pending"] >= MAX_PENDING or c["today"] >= MAX_PER_DAY:
            return {"error": f"too many scheduled tasks (max {MAX_PENDING} pending, {MAX_PER_DAY} per day); cancel some first"}
        row = await (await conn.execute(
            """INSERT INTO agent_tasks (tenant_id, run_at, kind, payload, created_by_session)
               VALUES (%s, %s, 'followup', %s, %s) RETURNING id""",
            (ctx.tenant_id, at, json.dumps({"instruction": instruction}), ctx.id))).fetchone()
    return {"task_id": str(row["id"]), "will_run_at_ist": at.strftime("%Y-%m-%d %H:%M"),
            "moved_out_of_quiet_hours": shifted}


async def list_followups(app, ctx: SessionCtx, args: dict) -> dict:
    async with app.state.pool.connection() as conn:
        rows = await (await conn.execute(
            """SELECT id, run_at, kind, payload FROM agent_tasks
                WHERE tenant_id = %s AND status = 'PENDING' ORDER BY run_at LIMIT 30""", (ctx.tenant_id,))).fetchall()
    return {"pending": [{"task_id": str(r["id"]), "run_at_ist": r["run_at"].astimezone(clock.IST).strftime("%Y-%m-%d %H:%M"),
                         "kind": r["kind"], "what": (r["payload"].get("instruction") or r["payload"].get("text") or "")[:200]}
                        for r in rows]}


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


async def send_file_to_owner(app, ctx: SessionCtx, args: dict) -> dict:
    from src import tenants
    ref = str(args["file_ref"])
    _, job_id, name = (ref.split(":", 2) + ["", ""])[:3]
    ext = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in SENDABLE:
        return {"error": f"cannot send {ext or 'this'} file; allowed: {', '.join(sorted(SENDABLE))}"}
    data = app.state.sandbox.read_output(job_id, name)
    if not data or len(data) > MAX_FILE_BYTES:
        return {"error": "file is empty or larger than 16 MB"}
    tenant = await tenants.by_id(app.state.pool, ctx.tenant_id)
    mime = SENDABLE.get(ext) or mimetypes.guess_type(name)[0] or "application/octet-stream"
    caption = re.sub(r"\s+", " ", str(args.get("caption") or ""))[:900]
    via = await send_document_to_owner(app, tenant, data, name, mime, caption)
    if via == "window_closed":
        return {"sent": False, "error": "the owner's 24-hour WhatsApp window is closed, so a file cannot be sent now; "
                                        "tell them in your reply or try again after they message"}
    return {"sent": True, "via": via, "filename": name, "bytes": len(data)}
