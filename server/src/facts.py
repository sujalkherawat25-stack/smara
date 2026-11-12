"""The owner's standing facts, one current row per topic (table shop_facts, migration 023).

A new value for a topic replaces the old one, which is remembered only as a superseded wording so the copy Continuum still
holds can be ignored. Facts with no topic never come here. Writing is best effort for the caller: see memory_pass.
"""
import logging
import re

from src import clock

log = logging.getLogger(__name__)
MAX_SHOWN = 20


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


async def upsert(pool, tenant_id: str, party_id: str | None, topic: str, text: str, said: str) -> str:
    """Set the current value of a topic. Returns 'new', 'updated' (the old wording is kept as superseded) or 'same'."""
    slot = f"{party_id or 'shop'}:{topic}"
    async with pool.connection() as conn:
        async with conn.transaction():
            old = await (await conn.execute("SELECT text, said FROM shop_facts WHERE tenant_id = %s AND slot = %s FOR UPDATE",
                                            (tenant_id, slot))).fetchone()
            if old is None:
                await conn.execute(
                    """INSERT INTO shop_facts (tenant_id, slot, party_id, topic, text, said) VALUES (%s, %s, %s, %s, %s, %s)""",
                    (tenant_id, slot, party_id, topic, text, said))
                return "new"
            if old["text"] == text:
                await conn.execute("UPDATE shop_facts SET said = %s, updated_at = now() WHERE tenant_id = %s AND slot = %s",
                                   (said or old["said"], tenant_id, slot))
                return "same"
            await conn.execute(
                """UPDATE shop_facts SET text = %s, said = %s, updated_at = now(),
                          superseded = array_append(array_remove(superseded, %s), %s)
                    WHERE tenant_id = %s AND slot = %s""",
                (text, said, compose(text, said), compose(old["text"], old["said"]), tenant_id, slot))
            return "updated"


async def current(pool, tenant_id: str, party_ids=(), limit: int = MAX_SHOWN) -> list[dict]:
    """The shop-wide facts and those about the given customers, newest first."""
    async with pool.connection() as conn:
        return await (await conn.execute(
            """SELECT f.topic, f.text, f.updated_at, p.canonical_name AS customer FROM shop_facts f
                 LEFT JOIN parties p ON p.id = f.party_id
                WHERE f.tenant_id = %s AND (f.party_id IS NULL OR f.party_id::text = ANY(%s::text[]))
                ORDER BY f.updated_at DESC LIMIT %s""", (tenant_id, [str(p) for p in party_ids], limit))).fetchall()


async def superseded_texts(pool, tenant_id: str) -> frozenset[str]:
    """Every wording that a newer value has replaced, as it was written to Continuum."""
    async with pool.connection() as conn:
        rows = await (await conn.execute(
            "SELECT unnest(superseded) AS t FROM shop_facts WHERE tenant_id = %s LIMIT 1000", (tenant_id,))).fetchall()
    return frozenset(r["t"] for r in rows)


def lines(rows: list[dict]) -> str:
    """The facts as prompt lines: who it is about, the fact, and when it was told."""
    out = []
    for r in rows:
        who = r["customer"] or "Shop"
        out.append(f"- {who}: {r['text']} (told {r['updated_at'].astimezone(clock.IST).strftime('%d %b')})")
    return "\n".join(out)
