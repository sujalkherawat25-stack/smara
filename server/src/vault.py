"""Encrypted per-shop secrets (envelope encryption).

Each shop gets its own random 256-bit data key. The data key is stored only wrapped (AES-256-GCM) by the server's
master key, and every secret is encrypted with the shop's data key, bound to (shop, name) so a ciphertext cannot be
moved to another shop or another name. Consequences:
  - a database dump or backup alone reveals nothing: the master key is not in the database;
  - erasing a shop destroys its data key, so every copy of its secrets (including backups) becomes unreadable;
  - the master key can be rotated by re-wrapping the small data keys, without touching the secrets.

Master key: VAULT_MASTER_KEY (base64, 32 bytes) or the file named by VAULT_MASTER_KEY_FILE (e.g. a Docker secret in
/run/secrets). Generate one with: python scripts/vault_admin.py new-key
Never store bank passwords, UPI PINs or card numbers here: Smara never handles those.
"""
import base64
import os
import secrets as _secrets
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

FORBIDDEN_NAMES = ("upi_pin", "card", "cvv", "netbanking", "bank_password", "atm_pin")


class VaultError(RuntimeError):
    pass


def _decode_key(raw: str) -> bytes:
    key = base64.b64decode(raw.strip())
    if len(key) != 32:
        raise VaultError("the vault master key must be 32 bytes (base64)")
    return key


def master_keys() -> dict[int, bytes]:
    """{version: key}. VAULT_MASTER_KEY is the current one; VAULT_MASTER_KEY_OLD (optional) is kept during rotation."""
    keys: dict[int, bytes] = {}
    path = os.environ.get("VAULT_MASTER_KEY_FILE", "")
    current = Path(path).read_text().strip() if path and Path(path).exists() else os.environ.get("VAULT_MASTER_KEY", "")
    if current:
        keys[int(os.environ.get("VAULT_MASTER_VERSION", "1"))] = _decode_key(current)
    if old := os.environ.get("VAULT_MASTER_KEY_OLD", ""):
        keys[int(os.environ.get("VAULT_MASTER_VERSION_OLD", "0"))] = _decode_key(old)
    return keys


def current_version() -> int:
    keys = master_keys()
    if not keys:
        raise VaultError("no vault master key configured (VAULT_MASTER_KEY)")
    return int(os.environ.get("VAULT_MASTER_VERSION", "1"))


def _aad(*parts: str) -> bytes:
    return "|".join(parts).encode()


def new_master_key() -> str:
    return base64.b64encode(_secrets.token_bytes(32)).decode()


async def _data_key(conn, tenant_id: str, create: bool) -> bytes | None:
    row = await (await conn.execute("SELECT wrapped_key, nonce, master_version FROM tenant_keys WHERE tenant_id = %s",
                                    (tenant_id,))).fetchone()
    keys = master_keys()
    if row:
        mk = keys.get(row["master_version"])
        if mk is None:
            raise VaultError(f"master key version {row['master_version']} is not configured")
        return AESGCM(mk).decrypt(bytes(row["nonce"]), bytes(row["wrapped_key"]), _aad("tenant-key", tenant_id))
    if not create:
        return None
    ver = current_version()
    dk, nonce = AESGCM.generate_key(256), _secrets.token_bytes(12)
    wrapped = AESGCM(keys[ver]).encrypt(nonce, dk, _aad("tenant-key", tenant_id))
    await conn.execute("""INSERT INTO tenant_keys (tenant_id, wrapped_key, nonce, master_version) VALUES (%s, %s, %s, %s)
                          ON CONFLICT (tenant_id) DO NOTHING""", (tenant_id, wrapped, nonce, ver))
    return await _data_key(conn, tenant_id, create=False)      # whoever won the race, use the stored key


def _check_name(name: str) -> None:
    if not name or len(name) > 100 or any(f in name.lower() for f in FORBIDDEN_NAMES):
        raise VaultError(f"refusing to store a secret named {name!r}")


async def put_secret(pool, tenant_id: str, name: str, value: str | bytes) -> None:
    _check_name(name)
    data = value.encode() if isinstance(value, str) else value
    async with pool.connection() as conn:
        async with conn.transaction():
            dk = await _data_key(conn, tenant_id, create=True)
            nonce = _secrets.token_bytes(12)
            ct = AESGCM(dk).encrypt(nonce, data, _aad("secret", tenant_id, name))
            await conn.execute(
                """INSERT INTO tenant_secrets (tenant_id, name, ciphertext, nonce) VALUES (%s, %s, %s, %s)
                   ON CONFLICT (tenant_id, name) DO UPDATE SET ciphertext = EXCLUDED.ciphertext, nonce = EXCLUDED.nonce,
                                                             updated_at = now()""", (tenant_id, name, ct, nonce))


async def get_secret(pool, tenant_id: str, name: str) -> bytes | None:
    async with pool.connection() as conn:
        row = await (await conn.execute("SELECT ciphertext, nonce FROM tenant_secrets WHERE tenant_id = %s AND name = %s",
                                        (tenant_id, name))).fetchone()
        if row is None:
            return None
        dk = await _data_key(conn, tenant_id, create=False)
    if dk is None:
        return None
    return AESGCM(dk).decrypt(bytes(row["nonce"]), bytes(row["ciphertext"]), _aad("secret", tenant_id, name))


async def delete_secret(pool, tenant_id: str, name: str) -> bool:
    async with pool.connection() as conn:
        cur = await conn.execute("DELETE FROM tenant_secrets WHERE tenant_id = %s AND name = %s", (tenant_id, name))
        return cur.rowcount > 0


async def list_secrets(pool, tenant_id: str) -> list[str]:
    async with pool.connection() as conn:
        rows = await (await conn.execute("SELECT name FROM tenant_secrets WHERE tenant_id = %s ORDER BY name",
                                         (tenant_id,))).fetchall()
    return [r["name"] for r in rows]


async def shred_tenant(pool, tenant_id: str) -> None:
    """Destroy the shop's data key and secrets: anything left in backups can no longer be decrypted."""
    async with pool.connection() as conn:
        await conn.execute("DELETE FROM tenant_secrets WHERE tenant_id = %s", (tenant_id,))
        await conn.execute("DELETE FROM tenant_keys WHERE tenant_id = %s", (tenant_id,))


async def rewrap_all(pool) -> int:
    """Master key rotation: re-wrap every shop's data key under the current master key. Secrets are untouched."""
    ver, done = current_version(), 0
    keys = master_keys()
    async with pool.connection() as conn:
        rows = await (await conn.execute("SELECT tenant_id FROM tenant_keys WHERE master_version <> %s", (ver,))).fetchall()
        for r in rows:
            tid = str(r["tenant_id"])
            dk = await _data_key(conn, tid, create=False)
            nonce = _secrets.token_bytes(12)
            wrapped = AESGCM(keys[ver]).encrypt(nonce, dk, _aad("tenant-key", tid))
            await conn.execute("UPDATE tenant_keys SET wrapped_key = %s, nonce = %s, master_version = %s, rotated_at = now() "
                               "WHERE tenant_id = %s", (wrapped, nonce, ver, tid))
            done += 1
    return done
