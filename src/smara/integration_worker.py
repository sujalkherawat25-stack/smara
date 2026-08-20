from __future__ import annotations

import asyncio
import json
import time

import httpx

from .config import settings
from .integrations import IntegrationExecutor
from .integration_oauth import refresh_google
from .store import TaskStore, open_task_store
from .vault import SecretVault


async def run_once(store: TaskStore, vault: SecretVault, worker_id: str = "integration-worker") -> bool:
    action = store.claim_integration_action(worker_id)
    if action is None:
        return False
    try:
        connection = store.integration_by_id(action["connection_id"])
        credential = store.encrypted_integration_credential(connection["id"])
        secret = vault.decrypt(credential["encrypted_secret"])
        if connection["provider"] in {"gmail", "calendar", "drive"}:
            token = json.loads(secret)
            expires_in = int(token.get("expires_in", 3600))
            if int(token.get("obtained_at", 0)) + expires_in - 60 <= int(time.time()):
                token = await refresh_google(token)
                secret = json.dumps(token)
                store.store_integration_credential(connection["account_id"], connection["provider"], "oauth_token", vault.encrypt(secret))
        async with httpx.AsyncClient(timeout=20) as http:
            result = await IntegrationExecutor(http).execute(connection["provider"], action["action"], action["payload"], secret)
        store.complete_integration_action(action["id"], worker_id, result=result)
    except Exception as exc:
        store.complete_integration_action(action["id"], worker_id, error=str(exc))
    return True


async def main() -> None:
    store = open_task_store(database_url=settings.database_url, database_path=settings.database_path)
    # A deployment can run safely before any integration is configured. It
    # never attempts to process approved external work without a vault key.
    if not settings.integration_master_key:
        while True:
            await asyncio.sleep(60)
    vault = SecretVault(settings.integration_master_key)
    while True:
        worked = await run_once(store, vault)
        await asyncio.sleep(0.2 if worked else 2)


if __name__ == "__main__":
    asyncio.run(main())
