import hashlib
from typing import Any

import httpx


def make_source_id(tenant_id: str, wamid: str, body: str) -> str:
    """Deterministic SHA provenance id; a webhook retry yields the same id."""
    digest = hashlib.sha256(f"{tenant_id}|{wamid}|{body}".encode()).hexdigest()
    return f"wa_{digest[:40]}"


def scope_user(tenant_id: str, party_id: str) -> str:
    """Continuum user_id for one party's memory (matches ^[A-Za-z0-9_-]+$)."""
    return f"{tenant_id}_{party_id}"


class ContinuumClient:
    def __init__(self, base_url: str, api_key: str, client: httpx.AsyncClient | None = None):
        self._http = client or httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30,
        )

    async def write_evidence(
        self,
        *,
        user_id: str,
        source_id: str,
        text: str,
        trusted: bool,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Write one source-backed record. Untrusted text is labelled so the
        extractor never treats a debtor's claim as ledger fact."""
        label = "TRUSTED" if trusted else "UNTRUSTED debtor claim, unverified"
        meta = {"trust": "trusted" if trusted else "untrusted", **(metadata or {})}
        resp = await self._http.post(
            "/memories",
            headers={"Idempotency-Key": source_id},
            json={
                "user_id": user_id,
                "messages": [{"role": "user", "content": f"[{label}] {text}"}],
                "source_id": source_id,
                "ingestion_mode": "hybrid",
                "metadata": meta,
            },
        )
        resp.raise_for_status()

    async def resolve(self, *, user_id: str, query: str, max_claims: int = 8) -> dict:
        """Party state card (why + proof). Never use this to list obligations."""
        resp = await self._http.post(
            "/memories/resolve",
            json={"user_id": user_id, "query": query, "max_claims": max_claims},
        )
        resp.raise_for_status()
        return resp.json()

    async def aclose(self) -> None:
        await self._http.aclose()
