"""The owner's standing facts, one current row per topic (table shop_facts, migration 023).

A new value for a topic replaces the old one, which is remembered only as a superseded wording so the copy Continuum still
holds can be ignored. Facts with no topic never come here. Writing is best effort for the caller: see memory_pass.
"""
import logging
import re

from src import clock

log = logging.getLogger(__name__)
MAX_SHOWN = 20          # facts shown in the prompt, and separately as many rules, so ordinary facts never push a rule out


def slug(text: str, limit: int = 40) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(text or "").lower()).strip("_")[:limit]


def compose(text: str, said: str) -> str:
    """The fact as written to Continuum: the sentence plus the owner's own words, so the detail survives extraction."""
    return f'{text} (owner\'s words: "{said}")' if said else text


async def topics(pool, tenant_id: str, limit: int = 30) -> list[dict]:
    """What is already on file, for the memory pass to reuse a topic when the owner changes something."""
    async with pool.connection() as conn:
        return await (await conn.execute(
            """SELECT f.topic, f.text, p.canonical_name AS customer FROM shop_facts f LEFT JOIN parties p ON p.id = f.party_id
                WHERE f.tenant_id = %s ORDER BY f.updated_at DESC LIMIT %s""", (tenant_id, limit))).fetchall()


async def upsert(pool, tenant_id: str, party_id: str | None, topic: str, text: str, said: str,
                 is_rule: bool = False) -> str:
    """Set the current value of a topic. Returns 'new', 'updated' (the old wording is kept as superseded) or 'same'.
    `is_rule`: the owner set it as a rule for Smara's work; a topic that was a rule stays one when its value changes."""
    slot = f"{party_id or 'shop'}:{topic}"
    async with pool.connection() as conn:
        async with conn.transaction():
            old = await (await conn.execute("SELECT text, said FROM shop_facts WHERE tenant_id = %s AND slot = %s FOR UPDATE",
                                            (tenant_id, slot))).fetchone()
            if old is None:
                await conn.execute(
                    """INSERT INTO shop_facts (tenant_id, slot, party_id, topic, text, said, is_rule)
                       VALUES (%s, %s, %s, %s, %s, %s, %s)""", (tenant_id, slot, party_id, topic, text, said, is_rule))
                return "new"
            if old["text"] == text:
                await conn.execute("""UPDATE shop_facts SET said = %s, is_rule = is_rule OR %s, updated_at = now()
                                       WHERE tenant_id = %s AND slot = %s""", (said or old["said"], is_rule, tenant_id, slot))
                return "same"
            await conn.execute(
                """UPDATE shop_facts SET text = %s, said = %s, is_rule = is_rule OR %s, updated_at = now(),
                          superseded = array_append(array_remove(superseded, %s), %s)
                    WHERE tenant_id = %s AND slot = %s""",
                (text, said, is_rule, compose(text, said), compose(old["text"], old["said"]), tenant_id, slot))
            return "updated"


async def current(pool, tenant_id: str, party_ids=(), limit: int = MAX_SHOWN) -> list[dict]:
    """The shop-wide facts and those about the given customers: the rules first, then the rest, each newest first and
    each up to `limit`."""
    async with pool.connection() as conn:
        return await (await conn.execute(
            """SELECT topic, text, said, is_rule, updated_at, customer FROM (
                 SELECT f.topic, f.text, f.said, f.is_rule, f.updated_at, p.canonical_name AS customer,
                        row_number() OVER (PARTITION BY f.is_rule ORDER BY f.updated_at DESC) AS n
                   FROM shop_facts f LEFT JOIN parties p ON p.id = f.party_id
                  WHERE f.tenant_id = %s AND (f.party_id IS NULL OR f.party_id::text = ANY(%s::text[]))) q
                WHERE n <= %s ORDER BY is_rule DESC, updated_at DESC""",
            (tenant_id, [str(p) for p in party_ids], limit))).fetchall()


async def superseded_texts(pool, tenant_id: str) -> frozenset[str]:
    """Every wording that a newer value has replaced, as it was written to Continuum."""
    async with pool.connection() as conn:
        rows = await (await conn.execute(
            "SELECT unnest(superseded) AS t FROM shop_facts WHERE tenant_id = %s LIMIT 1000", (tenant_id,))).fetchall()
    return frozenset(r["t"] for r in rows)


def lines(rows: list[dict]) -> str:
    """The facts as prompt lines: who it is about, the fact, when it was told, and the owner's own words for it (this is
    the one copy of the fact in the prompt, so the exact number or address they typed has to be on it)."""
    out = []
    for r in rows:
        who = r["customer"] or "Shop"
        words = f"; owner's words: \"{r['said']}\"" if r.get("said") else ""
        out.append(f"- {who}: {r['text']} (told {r['updated_at'].astimezone(clock.IST).strftime('%d %b')}{words})")
    return "\n".join(out)
