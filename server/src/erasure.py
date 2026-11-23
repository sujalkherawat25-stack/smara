"""'DELETE MY ACCOUNT': erase everything Smara holds about a shop, on the owner's exact words.

The approval is the exact phrase, nothing looser (not a lower-case version, not a voice note, not a button): a phrase typed
on purpose cannot be guessed by the model or produced by a customer message. Anything softer ("delete my data", "mera data
delete karo") only explains what to send. The same goes for emptying the file space with 'DELETE ALL FILES'.

What is erased, in this order:
  1. Continuum (the long-term memory): every user_id of the shop. If Continuum cannot be reached nothing at all is
     deleted and the owner is told to try again, so a half-deleted account never exists.
  2. The shop's file space on disk (deleted-but-restorable files included).
  3. The database: every row of the shop, then the shop itself, in one transaction.
Only a hash of the shop id, the time and three counts stay behind (erasure_log), so it can be shown that it was done.
"""
import asyncio
import hashlib
import logging
import re

from src import tenants
from src.bridge.continuum import scope_user
from src.ingest import owner_files

log = logging.getLogger(__name__)

ACCOUNT_PHRASE = "DELETE MY ACCOUNT"
FILES_PHRASE = "DELETE ALL FILES"
_SOFT_ACCOUNT = ("delete my data", "delete my account", "delete account", "delete all my data", "erase my data",
                 "mera data delete karo", "data delete karo", "account delete karo", "mera account delete karo",
                 "mera data hata do", "account band karo")
_SOFT_FILES = re.compile(r"(delete|clear|remove|empty|hatao|hata do|saaf)\s+(all|every|everything|saari|sabhi|sab)?\s*(my\s+)?"
                         r"(stored\s+)?(files?|storage|space)|(files?|storage)\s+(delete|clear|saaf)|saari files|sab files")

ASK_ACCOUNT = (
    "Yeh aapka poora account hata dega: shop, customers, baaki rakam, messages, files, saved skills aur Smara ki yaaddasht "
    "(memory), sab. Yeh wapas nahi aayega.\n\n"
    "Pakka hai to bilkul yahi likhkar bhejiye (capital letters mein, aise hi):\n" + ACCOUNT_PHRASE + "\n\n"
    "Sirf kuch files hatani hain to 'files' bhejiye.\n(Everything is erased for good: your shop, customers, balances, messages, "
    "files, skills and Smara's memory. To confirm, send exactly: " + ACCOUNT_PHRASE + ")")
ASK_FILES = (
    "Yeh aapki saari kept files hata dega (30 din tak 'restore <number>' se wapas aa sakti hain). Pakka hai to bilkul yahi "
    "bhejiye:\n" + FILES_PHRASE + "\n\n(This hides all your kept files; they can be restored for 30 days. To confirm, send "
    "exactly: " + FILES_PHRASE + ". To remove everything for good, delete the account instead.)")

# Every table that holds a shop's rows through tenant_id, children before parents. test_erasure checks this list is complete.
TENANT_TABLES = (
    "agent_sessions", "agent_tasks", "bridge_jobs", "bridge_devices", "bridge_pairings", "browser_tasks", "chat_messages",
    "customer_routing", "data_requests", "digest_log", "digest_optout", "proactive_optin", "llm_usage", "outbound_messages", "owner_inbound_log", "owner_prefs",
    "owner_skills", "desktop_logins", "owner_desktop_tokens", "owner_desktop_pairings", "owner_memory_sharing", "payables", "shop_facts", "tally_syncs", "tenant_keys", "tenant_secrets", "wa_link_daily_sends",
    "wa_links", "whatsapp_connect_links", "whatsapp_connections", "obligations", "parties",
)


def is_account_confirm(text: str) -> bool:
    return (text or "").strip() == ACCOUNT_PHRASE


def is_files_confirm(text: str) -> bool:
    return (text or "").strip() == FILES_PHRASE


def soft_account(cmd: str) -> bool:
    return cmd in _SOFT_ACCOUNT


def soft_files(cmd: str) -> bool:
    return bool(_SOFT_FILES.search(cmd)) and not re.fullmatch(r"(yes |haan )?(delete|hatao) (file )?#?\d+", cmd)


async def _continuum_users(pool, tenant_id: str) -> list[str]:
    async with pool.connection() as conn:
        rows = await (await conn.execute(
            "SELECT DISTINCT user_id FROM continuum_outbox WHERE starts_with(user_id, %s)", (f"{tenant_id}_",))).fetchall()
        parties = await (await conn.execute("SELECT id FROM parties WHERE tenant_id = %s", (tenant_id,))).fetchall()
    ids = {r["user_id"] for r in rows} | {scope_user(tenant_id, str(p["id"])) for p in parties} | {scope_user(tenant_id, "shop"), scope_user(tenant_id, "desktop")}
    return sorted(ids)


async def _erase_continuum(app, user_ids: list[str]) -> None:
    client = getattr(app.state, "continuum", None)
    if client is None:
        return
    sem = asyncio.Semaphore(8)

    async def one(uid: str) -> None:
        async with sem:
            await client.delete_user(uid)
    await asyncio.gather(*(one(u) for u in user_ids))


async def _erase_rows(pool, tenant, continuum_users: int = 0, files: int = 0) -> int:
    n = 0
    tid = tenant.id
    async with pool.connection() as conn:
        async with conn.transaction():
            async def run(sql: str, *args) -> None:
                nonlocal n
                n += (await conn.execute(sql, args)).rowcount or 0
            sub = "(SELECT id FROM obligations WHERE tenant_id = %s)"
            await run(f"DELETE FROM proposed_adjustments WHERE obligation_id IN {sub}", tid)
            await run(f"DELETE FROM obligation_events WHERE obligation_id IN {sub}", tid)
            await run("DELETE FROM continuum_outbox WHERE starts_with(user_id, %s)", f"{tid}_")
            for table in TENANT_TABLES:
                await run(f"DELETE FROM {table} WHERE tenant_id = %s", tid)
            await run("DELETE FROM onboarding_sessions WHERE owner_phone = %s OR phone = %s", tenant.owner_phone, tenant.owner_phone)
            await run("DELETE FROM waitlist WHERE phone = %s", tenant.owner_phone)
            if tenant.telegram_chat_id:
                await run("DELETE FROM processed_messages WHERE starts_with(wamid, %s)", f"tg:{tenant.telegram_chat_id}:")
            await run("DELETE FROM tenants WHERE id = %s", tid)
            await conn.execute("""INSERT INTO erasure_log (tenant_hash, rows_removed, continuum_users, files_removed)
                                  VALUES (%s, %s, %s, %s)""",
                               (hashlib.sha256(str(tid).encode()).hexdigest(), n, continuum_users, files))
    return n


async def erase_account(app, tenant_id: str) -> str | None:
    """Erase the whole account. Returns None when done, or the plain-words reason nothing was deleted."""
    pool = app.state.pool
    tenant = await tenants.by_id(pool, tenant_id)
    if tenant is None:
        return None
    users = await _continuum_users(pool, tenant_id)
    try:
        await _erase_continuum(app, users)
    except Exception:
        log.exception("account erasure stopped: Continuum could not delete")
        return ("Smara ki yaaddasht (memory) abhi hat nahi paayi, isliye kuch bhi delete nahi hua. Kuch minute baad "
                f"{ACCOUNT_PHRASE} dobara bhejiye.\n(Memory service did not respond, so nothing was deleted. Try again in a few minutes.)")
    files = await asyncio.to_thread(owner_files.erase_folder, app.state.settings.owner_files_dir, tenant_id)
    rows = await _erase_rows(pool, tenant, len(users), files)
    try:
        from src.ops_alert import ops_alert
        await ops_alert(app, f"erased-{hashlib.sha256(str(tenant_id).encode()).hexdigest()[:8]}",
                        f"Smara: {tenant.name} ne apna account delete kar diya ({rows} rows, {files} files, {len(users)} memory scopes).")
    except Exception:
        log.exception("could not tell the admins about an erasure")
    return None


async def _farewell(app, tenant, channel: str) -> None:
    text = ("Aapka account aur saara data (shop, customers, rakam, messages, files, yaaddasht) hata diya gaya hai. "
            "Dobara use karna ho to naya invite chahiye. Shukriya.\n(Your account and all data are deleted. Thank you.)")
    tg = getattr(app.state, "tg", None)
    if channel == "telegram" and tg is not None and tenant.telegram_chat_id:
        await tg.send({"chat_id": tenant.telegram_chat_id, "text": text})


async def handle_confirm(app, tenant_id: str, channel: str) -> None:
    """The owner sent the exact phrase. Sends its own reply: the shop (and its chat log) is gone afterwards."""
    tenant = await tenants.by_id(app.state.pool, tenant_id)
    if tenant is None:
        return
    tg = getattr(app.state, "tg", None)
    reason = await erase_account(app, tenant.id)
    if reason is None:
        await _farewell(app, tenant, channel)
    elif channel == "telegram" and tg is not None and tenant.telegram_chat_id:
        await tg.send({"chat_id": tenant.telegram_chat_id, "text": reason})


async def delete_all_files(app, tenant_id: str) -> str:
    from datetime import datetime, timezone
    n, size = await asyncio.to_thread(owner_files.delete_all, app.state.settings.owner_files_dir, tenant_id, datetime.now(timezone.utc))
    if not n:
        return "Aapki koi file kept nahi thi. (You had no kept files.)"
    return (f"{n} files ({size / (1024 * 1024):.1f} MB) hata di gayi. 30 din tak 'deleted files' bhejkar 'restore <number>' se "
            f"wapas la sakte hain.\n({n} files hidden; restorable for 30 days with 'restore <number>'.)")
