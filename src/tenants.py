"""Tenant directory. Small tables, so each lookup is a direct query (no cache to go stale)."""
from dataclasses import dataclass
from datetime import datetime

from psycopg_pool import AsyncConnectionPool

_COLS = ("id, name, owner_phone, upi_vpa, upi_name, wa_phone_number_id, owner_last_inbound_at, "
         "owner_nudged_at, telegram_chat_id, owner_last_channel")


@dataclass(frozen=True)
class Tenant:
    id: str
    name: str
    owner_phone: str
    upi_vpa: str | None = None
    upi_name: str | None = None
    wa_phone_number_id: str | None = None
    owner_last_inbound_at: datetime | None = None
    owner_nudged_at: datetime | None = None
    telegram_chat_id: int | None = None
    owner_last_channel: str | None = None

    @property
    def upi(self) -> dict | None:
        return {"vpa": self.upi_vpa, "name": self.upi_name or self.name} if self.upi_vpa else None


def _row(r) -> Tenant | None:
    return Tenant(**{**r, "id": str(r["id"])}) if r else None


async def _one(pool: AsyncConnectionPool, where: str, arg) -> Tenant | None:
    async with pool.connection() as conn:
        cur = await conn.execute(f"SELECT {_COLS} FROM tenants WHERE {where} = %s", (arg,))
        return _row(await cur.fetchone())


async def by_id(pool, tenant_id: str) -> Tenant | None:
    return await _one(pool, "id", tenant_id)


async def by_owner_phone(pool, phone: str) -> Tenant | None:
    return await _one(pool, "owner_phone", phone)


async def by_number_id(pool, number_id: str) -> Tenant | None:
    return await _one(pool, "wa_phone_number_id", number_id)


async def all_tenants(pool) -> list[Tenant]:
    async with pool.connection() as conn:
        cur = await conn.execute(f"SELECT {_COLS} FROM tenants ORDER BY created_at")
        return [_row(r) for r in await cur.fetchall()]


async def create(pool, *, name: str, owner_phone: str, upi_vpa: str | None = None,
                 upi_name: str | None = None, wa_phone_number_id: str | None = None,
                 tenant_id: str | None = None) -> Tenant:
    """Idempotent on owner_phone: re-running updates the existing tenant."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            f"""INSERT INTO tenants (id, name, owner_phone, upi_vpa, upi_name, wa_phone_number_id)
                VALUES (COALESCE(%s::uuid, gen_random_uuid()), %s, %s, %s, %s, %s)
                ON CONFLICT (owner_phone) DO UPDATE SET name = EXCLUDED.name, upi_vpa = EXCLUDED.upi_vpa,
                  upi_name = EXCLUDED.upi_name, wa_phone_number_id = EXCLUDED.wa_phone_number_id
                RETURNING {_COLS}""",
            (tenant_id, name, owner_phone, upi_vpa, upi_name, wa_phone_number_id))
        return _row(await cur.fetchone())


async def by_telegram_chat(pool, chat_id: int) -> Tenant | None:
    return await _one(pool, "telegram_chat_id", chat_id)


async def touch_owner_inbound(pool, tenant_id: str, channel: str = "whatsapp") -> None:
    """The owner just wrote. Remember where; only a WhatsApp message opens WhatsApp's 24h window."""
    async with pool.connection() as conn:
        await conn.execute(
            """UPDATE tenants SET owner_last_channel = %(c)s, owner_last_active_at = now(),
                      owner_last_inbound_at = CASE WHEN %(c)s = 'whatsapp' THEN now() ELSE owner_last_inbound_at END
                WHERE id = %(t)s""", {"c": channel, "t": tenant_id})
