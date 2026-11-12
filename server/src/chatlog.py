"""The conversation log: what the owner and Smara said, on every channel, kept in Postgres.

WhatsApp and Telegram give no way to fetch past messages, and the agent's own session is cut after 30 quiet minutes and
compacted when long. This log is what lets a new chat start with the recent conversation and lets the agent search
what was said weeks ago. It holds the owner's words and Smara's replies (cards and digests included), never tool calls,
customer messages or file contents. Writing is best effort: a failed write is logged and never breaks a reply.
"""
import logging
from contextvars import ContextVar

from src import clock

log = logging.getLogger(__name__)

# The agent run working right now (set by agent_loop), so what Smara sends during it is tied to it.
CURRENT_SESSION_ID: ContextVar[str | None] = ContextVar("current_session_id", default=None)

MAX_STORED = 4000
RECENT_DAYS = 30
RECENT_LINES = 40
RECENT_CHARS = 7000        # about 2,000 tokens in the prompt
LINE_CHARS = 400
CHANNEL_NAMES = {"telegram": "Telegram", "whatsapp": "WhatsApp", "wa_link": "WhatsApp"}


def text_of(payload: dict) -> str:
    """The words of a WhatsApp-shaped payload (the format every owner message is built in)."""
    if not isinstance(payload, dict):
        return ""
    if payload.get("type") == "interactive":
        return str(((payload.get("interactive") or {}).get("body") or {}).get("text", "")).strip()
    return str((payload.get("text") or {}).get("body", "")).strip()


async def record(pool, tenant_id: str, channel: str, role: str, text: str, *, kind: str = "text",
                 session_id: str | None = None, ref: str | None = None) -> int | None:
    """Add one line. Returns its id, or None when there was nothing to keep or the write failed."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        async with pool.connection() as conn:
            row = await (await conn.execute(
                """INSERT INTO chat_messages (tenant_id, channel, role, kind, content, session_id, ref)
                   VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                (tenant_id, channel or "whatsapp", role, kind, text[:MAX_STORED], session_id, ref))).fetchone()
        return row["id"]
    except Exception:
        log.warning("could not record a chat message for %s", tenant_id, exc_info=True)
        return None


async def attach(pool, tenant_id: str, ref: str, session_id: str) -> None:
    """The owner message with this channel id started (or joined) this agent run."""
    if not ref:
        return
    try:
        async with pool.connection() as conn:
            await conn.execute("UPDATE chat_messages SET session_id = %s WHERE tenant_id = %s AND ref = %s AND role = 'owner'",
                               (session_id, tenant_id, ref))
    except Exception:
        log.warning("could not tie a chat message to its run", exc_info=True)


def _clip(text: str, n: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "..."


def _when(ts) -> str:
    return ts.astimezone(clock.IST).strftime("%d %b %H:%M")


def _who(role: str) -> str:
    return "Owner" if role == "owner" else "You"


async def recent(pool, tenant_id: str, exclude_session: str | None = None) -> str:
    """The latest conversation, oldest first, across all channels and runs, for the start of a run. Lines of the run
    that is starting are left out: it already holds them in its own context."""
    async with pool.connection() as conn:
        rows = await (await conn.execute(
            """SELECT created_at, channel, role, content FROM chat_messages
                WHERE tenant_id = %s AND created_at > now() - make_interval(days => %s)
                  AND (%s::uuid IS NULL OR session_id IS DISTINCT FROM %s::uuid)
                ORDER BY created_at DESC, id DESC LIMIT %s""",
            (tenant_id, RECENT_DAYS, exclude_session, exclude_session, RECENT_LINES))).fetchall()
    lines, used = [], 0
    for r in rows:                                   # newest first, until the budget is spent
        line = f"[{_when(r['created_at'])} {CHANNEL_NAMES.get(r['channel'], r['channel'])}] {_who(r['role'])}: {_clip(r['content'], LINE_CHARS)}"
        if used + len(line) > RECENT_CHARS and lines:
            break
        lines.append(line)
        used += len(line)
    return "\n".join(reversed(lines))


async def search(pool, tenant_id: str, query: str, days: int = 90, limit: int = 8) -> list[dict]:
    """Messages whose words match the query. Every word counts, so a few matching words beat one; newest first on a tie.
    Plain substring matching on purpose: Hinglish spellings vary, and one shop's log is small."""
    words = []
    for w in query.replace("\n", " ").split():
        w = "".join(ch for ch in w if ch.isalnum())
        if len(w) >= 3 and w.lower() not in (x.lower() for x in words):
            words.append(w)
    if not words:
        return []
    patterns = [f"%{w}%" for w in words[:6]]
    async with pool.connection() as conn:
        rows = await (await conn.execute(
            """SELECT created_at, channel, role, content,
                      (SELECT count(*) FROM unnest(%(p)s::text[]) AS pat WHERE content ILIKE pat) AS hits
                 FROM chat_messages
                WHERE tenant_id = %(t)s AND created_at > now() - make_interval(days => %(d)s)
                  AND content ILIKE ANY(%(p)s::text[])
                ORDER BY hits DESC, created_at DESC LIMIT %(n)s""",
            {"t": tenant_id, "d": days, "p": patterns, "n": limit})).fetchall()
    return [{"when": _when(r["created_at"]), "channel": CHANNEL_NAMES.get(r["channel"], r["channel"]),
             "who": _who(r["role"]), "text": _clip(r["content"], 600)} for r in rows]
