"""Daily limits per account, so one heavy or abusive user cannot stall everyone on the shared model key.

Counted over the last 24 hours (a rolling window). A message that is turned back is not counted, so trying again never
pushes the wait further. Never limited: button taps (an owner must always be able to approve or reject what is waiting),
accounts flagged limits_exempt, and the commands in free_command(). Those cost no model time, and a person must always be
able to get help, see their files, stop messages, disconnect a PC and delete their data.

When someone is stopped the Smara team is told (once a day per account), and can give that account 2 to 10 times the
normal limits for 1 to 90 days from the admin WhatsApp number (/limit). There is no "unlimited" from chat.
"""
import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from src import clock, erasure, prefs

log = logging.getLogger(__name__)
DEFAULTS = {"msgs": 300, "files": 30, "tokens": 4_000_000}
MAX_TIMES, MAX_DAYS = 10, 90
DAY = timedelta(hours=24)
# Typed commands that Smara answers by itself, with no model (owner_flow). Compared after command_of().
FREE_COMMANDS = frozenset({
    "help", "commands", "menu",
    "digest off", "summary off", "weekly off", "digest band", "digest band karo", "digest stop",
    "unpair browser", "disconnect browser", "stop desktop approvals", "desktop approvals off", "unpair desktop approvals",
    "stop sharing desktop memory", "desktop memory off", "stop desktop memory", "forget desktop memory", "delete desktop memory",
    "telegram off",
    "follow-up off", "follow up off", "followup off", "follow-ups off", "follow ups off", "followups off", "check-in off",
    "checkin off", "follow-up band karo", "follow up band karo", "followup band karo"})


def command_of(text: str) -> str:
    """The text as owner_flow compares it with its commands: no leading slash, lower case, no apostrophes, single spaces."""
    text = (text or "").strip()
    if text.startswith("/"):
        text = text[1:].split("@")[0] if " " not in text else text[1:]
    return " ".join(text.lower().replace("'", "").split())


def free_command(msg) -> bool:
    """True for a typed command that costs no model time. A voice note or a file is never one."""
    from src.ingest import owner_file_commands
    if msg.kind != "text" or not msg.text:
        return False
    raw = msg.text.strip()
    if erasure.is_account_confirm(raw) or erasure.is_files_confirm(raw):
        return True
    cmd = command_of(raw)
    return (cmd in FREE_COMMANDS or erasure.soft_account(cmd) or (len(cmd) <= 40 and erasure.soft_files(cmd))
            or owner_file_commands.is_command(cmd))


def kind_of(msg) -> str:
    if msg.kind in ("document", "image", "video"):
        return "file"
    if msg.kind == "audio":
        return "audio"
    return "text"


def times(tenant, now: datetime | None = None) -> int:
    """How many times the normal limits this account has right now (1 unless an admin raised it and it has not run out)."""
    until = getattr(tenant, "limit_until", None)
    if until and until > (now or datetime.now(timezone.utc)):
        return max(1, min(int(getattr(tenant, "limit_times", 1) or 1), MAX_TIMES))
    return 1


def allowance(settings, tenant) -> dict:
    n = times(tenant)
    return {what: int(getattr(settings, f"owner_daily_{what}", base)) * n for what, base in DEFAULTS.items()}


async def record(pool, tenant_id: str, kind: str) -> None:
    async with pool.connection() as conn:
        await conn.execute("INSERT INTO owner_inbound_log (tenant_id, kind) VALUES (%s, %s)", (tenant_id, kind))


async def usage(pool, tenant_id: str) -> dict:
    """What this account used in the last 24 hours."""
    async with pool.connection() as conn:
        row = await (await conn.execute(
            """SELECT (SELECT count(*) FROM owner_inbound_log WHERE tenant_id = %(t)s AND created_at > now() - interval '24 hours') AS msgs,
                      (SELECT count(*) FROM owner_inbound_log WHERE tenant_id = %(t)s AND kind = 'file'
                          AND created_at > now() - interval '24 hours') AS files,
                      (SELECT coalesce(sum(total_tokens), 0) FROM llm_usage WHERE tenant_id = %(t)s
                          AND created_at > now() - interval '24 hours') AS tokens""", {"t": tenant_id})).fetchone()
    return {what: int(row[what]) for what in DEFAULTS}


@dataclass(frozen=True)
class Hit:
    what: str              # msgs | files | tokens
    limit: int
    used: int
    free_at: datetime      # when enough of it has aged out of the 24 hours for the next one to go through


async def free_at(pool, tenant_id: str, what: str, used: int, limit: int) -> datetime:
    async with pool.connection() as conn:
        if what == "tokens":                       # the first time at which what is left of the window is under the limit
            row = await (await conn.execute(
                """SELECT created_at FROM (SELECT created_at, id,
                          sum(total_tokens) OVER () - sum(total_tokens) OVER (ORDER BY created_at, id) AS still_counted
                     FROM llm_usage WHERE tenant_id = %s AND created_at > now() - interval '24 hours') u
                    WHERE still_counted < %s ORDER BY created_at, id LIMIT 1""", (tenant_id, limit))).fetchone()
        else:                                      # the (used - limit + 1)th oldest must age out
            only = " AND kind = 'file'" if what == "files" else ""
            row = await (await conn.execute(
                f"""SELECT created_at FROM owner_inbound_log WHERE tenant_id = %s{only}
                       AND created_at > now() - interval '24 hours' ORDER BY created_at, id OFFSET %s LIMIT 1""",
                (tenant_id, max(0, used - limit)))).fetchone()
    return (row["created_at"] if row else datetime.now(timezone.utc)) + DAY


async def reached(pool, settings, tenant, kind: str = "text") -> Hit | None:
    """The limit that stops this account's next message of this kind, or None. With more than one, the one that lasts longest."""
    lim, used = allowance(settings, tenant), await usage(pool, tenant.id)
    over = [w for w in ("msgs", "tokens", "files") if used[w] >= lim[w] and (w != "files" or kind == "file")]
    hits = [Hit(w, lim[w], used[w], await free_at(pool, tenant.id, w, used[w], lim[w])) for w in over]
    return max(hits, key=lambda h: h.free_at) if hits else None


def when_text(at: datetime, now: datetime | None = None, hinglish: bool = False) -> str:
    """'4:15 PM today' in Indian time, rounded up to the next five minutes."""
    at = at.astimezone(clock.IST)
    at = datetime.fromtimestamp(math.ceil(at.timestamp() / 300) * 300, clock.IST)
    days = (at.date() - (now or clock.now()).astimezone(clock.IST).date()).days
    clock_time = f"{at.hour % 12 or 12}:{at.minute:02d} {'AM' if at.hour < 12 else 'PM'}"
    if hinglish:
        day = {0: "aaj", 1: "kal"}.get(days, at.strftime("%d %b"))
        return f"{day} {clock_time}"
    day = {0: "today", 1: "tomorrow"}.get(days, "on " + at.strftime("%d %b"))
    return f"{clock_time} {day}"


def stop_message(hit: Hit, team_told: bool, hinglish: bool = False) -> str:
    """What the person reads. Every sentence has to be true: what stopped, until when, and what still works."""
    when = when_text(hit.free_at, hinglish=hinglish)
    if hinglish:
        first = {"msgs": f"24 ghante ki {hit.limit} messages wali limit poori ho gayi. Lagbhag {when} IST se aap phir message bhej sakte hain.",
                 "files": f"24 ghante ki {hit.limit} files wali limit poori ho gayi. Aap likh kar baat kar sakte hain; files lagbhag {when} IST se phir bhej sakte hain.",
                 "tokens": f"Aaj ka AI kaam ka hissa poora ho gaya. Lagbhag {when} IST se main phir kaam kar paungi."}[hit.what]
        tail = "\n\n'help', 'files' aur apna data delete karna abhi bhi chalta hai."
        return first + tail + (" Maine Smara team ko bata diya hai; wo aapki limit badha sakte hain." if team_told else "")
    first = {"msgs": f"You've reached today's limit on Smara ({hit.limit} messages in 24 hours). You can send messages again from about {when} IST.",
             "files": f"You've reached today's limit of {hit.limit} files in 24 hours. You can still type to me, and send files again from about {when} IST.",
             "tokens": f"You've used today's AI allowance on Smara. I can work for you again from about {when} IST."}[hit.what]
    tail = "\n\n'help', 'files' and deleting your data still work."
    return first + tail + (" I've told the Smara team; they can raise your limit." if team_told else "")


async def alert_team(app, tenant, hit: Hit) -> bool:
    """Tell the admins this account was stopped, once a day. True if they have been told (now, or earlier today)."""
    pool = app.state.pool
    async with pool.connection() as conn:
        claimed = await (await conn.execute(
            """UPDATE tenants SET limit_alerted_at = now() WHERE id = %s
                  AND (limit_alerted_at IS NULL OR limit_alerted_at < now() - interval '24 hours') RETURNING id""",
            (tenant.id,))).fetchone()
    if claimed is None:
        return True
    try:
        from src.ingest import admin_wa
        told = await admin_wa.tell_admins_about_limit(app, tenant, hit)
    except Exception:
        log.exception("could not tell the admins that an account reached its limit")
        told = False
    if not told:                    # nobody got it: say nothing to the user about it, and try again next time
        async with pool.connection() as conn:
            await conn.execute("UPDATE tenants SET limit_alerted_at = NULL WHERE id = %s", (tenant.id,))
    return told


async def admit(app, tenant, msg) -> str | None:
    """None if this message may go on (it is then counted), else the sentence to send instead of running the agent."""
    if msg.button_id or free_command(msg):
        return None
    pool, kind = app.state.pool, kind_of(msg)
    if not getattr(tenant, "limits_exempt", False):
        if hit := await reached(pool, app.state.settings, tenant, kind):
            told = await alert_team(app, tenant, hit)
            language = (await prefs.get(pool, tenant.id)).get("language", "auto")
            log.info("account %s stopped by its daily %s limit", tenant.id, hit.what)
            return stop_message(hit, told, hinglish=language in ("hinglish", "hindi"))
    await record(pool, tenant.id, kind)
    return None
