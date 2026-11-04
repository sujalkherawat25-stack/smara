"""After the owner's turn: keep what is worth remembering for months.

The agent only writes memory when it decides to, and a small model often does not. This pass reads ONLY what the owner
typed in the turn (never customer text, file contents or web pages) and Smara's reply, asks the light model for durable
facts, and writes them to Continuum through the same outbox as every other write, so they are ordered and retried.
Skipped when the agent already saved something itself this turn. Best effort: it never affects the conversation.
"""
import logging
import re

from src.agent.prompt import forget_standing
from src.bridge.continuum import scope_user
from src.bridge.outbox import enqueue_standalone

log = logging.getLogger(__name__)
MAX_FACTS = 5
SYSTEM = (
    "You decide what an Indian small-business back-office assistant should remember for months. You are given what the "
    "business owner wrote this turn and the assistant's reply. Output JSON {\"facts\": [{\"about\": \"shop\" or \"customer\", "
    "\"customer\": \"name exactly as the owner wrote it, or empty\", \"fact\": \"one short sentence\"}]}.\n"
    "Keep ONLY durable facts the owner stated: a customer's payment habits, promises, disputes or relationship; the owner's "
    "standing rules and preferences; business details (payment terms, GSTIN, bank or payment days, staff, working hours).\n"
    "Do NOT keep: questions, one-off requests, greetings, amounts or dates already in the ledger, anything only the assistant "
    "said, guesses. Never invent. Write each fact in English, about 20 words at most. If nothing qualifies return {\"facts\": []}."
)


def _owner_text(messages: list[dict]) -> str:
    """Owner messages of the latest turn only."""
    last_user = max((i for i, m in enumerate(messages) if m["role"] == "user" and not str(m["content"]).startswith("[system note]")),
                    default=-1)
    if last_user < 0:
        return ""
    return str(messages[last_user]["content"])[:1500]


def _agent_already_saved(messages: list[dict]) -> bool:
    last_user = max((i for i, m in enumerate(messages) if m["role"] == "user" and not str(m["content"]).startswith("[system note]")),
                    default=-1)
    return any(c["function"]["name"] == "write_continuum_evidence"
               for m in messages[last_user + 1:] for c in (m.get("tool_calls") or []))


async def _party_id(pool, tenant_id: str, name: str) -> str | None:
    name = re.sub(r"\s+", " ", name).strip()
    if len(name) < 3:
        return None
    async with pool.connection() as conn:
        rows = await (await conn.execute(
            """SELECT id FROM parties WHERE tenant_id = %s AND (canonical_name ILIKE %s OR EXISTS
                 (SELECT 1 FROM unnest(aliases) a WHERE a ILIKE %s)) LIMIT 2""",
            (tenant_id, f"%{name}%", f"%{name}%"))).fetchall()
    return str(rows[0]["id"]) if len(rows) == 1 else None


async def remember_from_turn(app, tenant_id: str, session_id: str, messages: list[dict]) -> int:
    llm = getattr(app.state, "llm", None)
    ask = getattr(llm, "json", None)
    if not callable(ask) or not getattr(llm, "enabled", False) or _agent_already_saved(messages):
        return 0
    owner_said = _owner_text(messages)
    reply = next((str(m.get("content") or "") for m in reversed(messages) if m["role"] == "assistant" and not m.get("tool_calls")), "")
    if len(owner_said.strip()) < 12:
        return 0
    try:
        out = await ask(SYSTEM, f"Owner wrote:\n{owner_said}\n\nAssistant replied:\n{reply[:800]}")
        facts = [f for f in (out.get("facts") or []) if isinstance(f, dict) and str(f.get("fact", "")).strip()][:MAX_FACTS]
    except Exception:
        log.info("memory pass skipped: model unavailable")
        return 0
    written = 0
    for i, f in enumerate(facts):
        text = re.sub(r"\s+", " ", str(f["fact"])).strip()[:300]
        pid = await _party_id(app.state.pool, tenant_id, str(f.get("customer") or "")) if f.get("about") == "customer" else None
        scope = scope_user(tenant_id, pid or "shop")
        if f.get("about") == "customer" and not pid and f.get("customer"):
            text = f"About {str(f['customer']).strip()[:60]}: {text}"        # not in the ledger: keep the name with the fact
        try:
            await enqueue_standalone(app.state.pool, user_id=scope, source_id=f"mem_{session_id}_{i}"[:200], text=text,
                                     trusted=True, metadata={"actor": "owner", "session": session_id, "via": "memory_pass"})
            written += 1
        except Exception:
            log.warning("memory pass could not queue a fact", exc_info=True)
    if written:
        forget_standing(tenant_id)
    return written
