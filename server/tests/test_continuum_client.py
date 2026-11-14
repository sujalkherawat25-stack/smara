import json

import httpx
import pytest

from src.bridge.continuum import ContinuumClient


async def _sent(trusted: bool) -> dict:
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(req.content)
        seen["key"] = req.headers.get("Idempotency-Key")
        return httpx.Response(202, json={"event_id": "e1", "status": "pending"})

    http = httpx.AsyncClient(base_url="http://c", transport=httpx.MockTransport(handler))
    await ContinuumClient("http://c", "k", client=http).write_evidence(
        user_id="t_p1", source_id="src1", text="Sharma says paid", trusted=trusted, metadata={"actor": "UNTRUSTED"})
    seen["_"] = None
    return seen


@pytest.mark.asyncio
async def test_records_are_written_as_agent_records_with_trust_label():
    s = await _sent(trusted=False)
    b = s["body"]
    assert b["capture_contract"] == "agent_records_v1"      # records about customers, never "facts about the user"
    assert b["metadata"]["trust"] == "untrusted"            # Continuum never lets this retire an owner fact
    assert b["metadata"]["actor"] == "UNTRUSTED"
    assert b["messages"][0]["content"].startswith("[UNTRUSTED debtor claim, unverified]")
    assert s["key"] == "src1" and b["source_id"] == "src1"


@pytest.mark.asyncio
async def test_owner_writes_are_trusted():
    b = (await _sent(trusted=True))["body"]
    assert b["metadata"]["trust"] == "trusted" and b["capture_contract"] == "agent_records_v1"
