"""After the owner's turn: keep what is worth remembering for months.

The agent only writes memory when it decides to, and a small model often does not. This pass reads ONLY what the owner
typed in the turn (never customer text, file contents or web pages) and Smara's reply, asks the light model for durable
facts, and writes them to Continuum through the same outbox as every other write, so they are ordered and retried.
Skipped when the agent already saved something itself this turn. Best effort: it never affects the conversation.

Each fact keeps the owner's own words next to the sentence (so a number or an address survives the model's paraphrase) and
says who pays whom. A fact about something that can change (a rule, a limit, an address, a habit) carries a topic; a new
value for the same topic replaces the old one in shop_facts (see facts.py), which is what the prompt shows as current.
"""
import logging
import re

from src import facts as shop_facts
from src.agent.prompt import forget_standing
from src.bridge.continuum import scope_user
from src.bridge.outbox import enqueue_standalone

log = logging.getLogger(__name__)
MAX_FACTS = 5
SAID_CHARS = 200
SYSTEM = (
    "You decide what an Indian small-business back-office assistant should remember for months. You are given what the "
    "business owner wrote this turn, the assistant's reply, and the facts already on file. Output JSON "
    "{\"facts\": [{\"about\": \"shop\" or \"customer\", \"customer\": \"name exactly as the owner wrote it, or empty\", "
    "\"topic\": \"...\", \"fact\": \"...\", \"said\": \"...\"}]}.\n"
    "Keep ONLY durable facts the owner stated: a customer's payment habits, promises, disputes or relationship; the owner's "
    "standing rules and preferences; business details (payment terms, GSTIN, bank or payment days, staff, working hours, addresses).\n"
    "Do NOT keep: questions, one-off requests, greetings, amounts or dates already in the ledger, anything only the assistant "
    "said, guesses. Never invent.\n"
    "fact: one self-contained sentence in English, about 25 words at most, naming the person or business. Keep WHO PAYS WHOM "
    "exactly: 'X ko ... dene hain' means the owner must pay X; 'X ne bola ... dega' means X will pay the owner.\n"
    "said: the owner's own words for it, copied exactly from what they wrote, 150 characters at most.\n"
    "topic: for something that stays true until it changes (a reminder rule, a credit limit, a payment habit, an address, who "
    "does what) give a short snake_case label such as reminder_rule, credit_limit, payment_habit, godown_address, tally_operator. "
    "If the owner is changing or repeating something already on file, use EXACTLY the topic it has there and write fact as the "
    "new full value (for example 'changed to ...'). Leave topic empty for a one-off thing such as a promise or a dispute, which "
    "sits next to others instead of replacing them. If nothing qualifies return {\"facts\": []}."
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


async def _on_file(pool, tenant_id: str) -> str:
    try:
        rows = await shop_facts.topics(pool, tenant_id)
    except Exception:
        log.warning("memory pass could not read the facts on file", exc_info=True)
        return ""
    if not rows:
        return "Facts already on file: none."
    return "Facts already on file (topic | about | fact):\n" + "\n".join(
        f"{r['topic']} | {r['customer'] or 'shop'} | {r['text'][:160]}" for r in rows)


async def remember_from_turn(app, tenant_id: str, session_id: str, messages: list[dict]) -> int:
    llm = getattr(app.state, "llm", None)
    ask = getattr(llm, "json", None)
    if not callable(ask) or not getattr(llm, "enabled", False) or _agent_already_saved(messages):
        return 0
    owner_said = _owner_text(messages)
    reply = next((str(m.get("content") or "") for m in reversed(messages) if m["role"] == "assistant" and not m.get("tool_calls")), "")
    if len(owner_said.strip()) < 12:
        return 0
    pool = app.state.pool
    try:
        out = await ask(SYSTEM, f"Owner wrote:\n{owner_said}\n\nAssistant replied:\n{reply[:800]}\n\n{await _on_file(pool, tenant_id)}")
        found = [f for f in (out.get("facts") or []) if isinstance(f, dict) and str(f.get("fact", "")).strip()][:MAX_FACTS]
    except Exception:
        log.info("memory pass skipped: model unavailable")
        return 0
    written = 0
    for i, f in enumerate(found):
        text = re.sub(r"\s+", " ", str(f["fact"])).strip()[:300]
        said = re.sub(r"\s+", " ", str(f.get("said") or "")).strip()[:SAID_CHARS]
        topic = shop_facts.slug(f.get("topic") or "")
        about_customer = f.get("about") == "customer"
        customer = str(f.get("customer") or "").strip()
        pid = await _party_id(pool, tenant_id, customer) if about_customer else None
        scope = scope_user(tenant_id, pid or "shop")
        if about_customer and not pid and customer:
            text = f"About {customer[:60]}: {text}"        # not in the ledger: keep the name with the fact
            topic = f"{topic}_{shop_facts.slug(customer, 20)}" if topic else ""
        try:
            if topic:
                await shop_facts.upsert(pool, tenant_id, pid, topic, text, said)
            await enqueue_standalone(pool, user_id=scope, source_id=f"mem_{session_id}_{i}"[:200],
                                     text=shop_facts.compose(text, said), trusted=True,
                                     metadata={"actor": "owner", "session": session_id, "via": "memory_pass", "topic": topic})
            written += 1
        except Exception:
            log.warning("memory pass could not queue a fact", exc_info=True)
    if written:
        forget_standing(tenant_id)
    return written
