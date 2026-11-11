"""Live Continuum round trip with a throwaway scope: write -> ingest -> resolve -> delete.

    docker exec smara_app python scripts/continuum_check.py

Uses CONTINUUM_URL / CONTINUUM_API_KEY from the environment. Writes only under a
`smara-probe-*` user id and deletes it at the end. Exit code 1 if any step fails.
"""
import asyncio
import os
import sys
import time
from uuid import uuid4

import httpx

MARKER = "INV-PROBE-" + uuid4().hex[:6].upper()


async def main() -> int:
    base, key = os.environ["CONTINUUM_URL"], os.environ["CONTINUUM_API_KEY"]
    scope = f"smara-probe-{uuid4().hex[:10]}"
    failures = 0

    def step(name: str, ok: bool, detail: str = "") -> None:
        nonlocal failures
        failures += not ok
        print(f"{'OK  ' if ok else 'FAIL'} {name:<34} {detail}", flush=True)

    async with httpx.AsyncClient(base_url=base, headers={"Authorization": f"Bearer {key}"}, timeout=60) as h:
        t = time.monotonic()
        text = f"[TRUSTED] Ravi Kirana promised to pay invoice {MARKER} of Rs 12,500 by 15 November 2026."
        w = await h.post("/memories", headers={"Idempotency-Key": scope}, json={
            "user_id": scope, "source_id": scope, "ingestion_mode": "hybrid",
            "messages": [{"role": "user", "content": text}], "metadata": {"trust": "trusted", "probe": True}})
        step("write accepted (202)", w.status_code == 202, f"{w.status_code} in {time.monotonic() - t:.2f}s")
        found, waited = "", 0.0
        t = time.monotonic()
        while time.monotonic() - t < 120:
            r = await h.post("/memories/resolve", json={"user_id": scope, "query": f"when will Ravi pay {MARKER}", "max_claims": 4})
            body = r.json() if r.status_code == 200 else {}
            blob = str(body.get("resolution", {}).get("state_card", "")) + str(body.get("context", ""))
            if MARKER in blob or "15 November" in blob:
                found = blob
                break
            await asyncio.sleep(3)
        waited = time.monotonic() - t
        step("ingested + resolvable", bool(found), f"after {waited:.1f}s")
        step("answer carries the promise", "15" in found and "Nov" in found, found[:90].replace("\n", " "))
        s = await h.post("/memories/search", json={"user_id": scope, "query": MARKER, "top_k": 3})
        step("search returns it", s.status_code == 200 and bool(s.json().get("results")), f"{s.status_code}")
        iso = await h.post("/memories/resolve", json={"user_id": f"smara-probe-other-{uuid4().hex[:6]}", "query": MARKER})
        leak = MARKER in str(iso.json()) if iso.status_code == 200 else False
        step("other scope cannot see it", not leak)
        d = await h.delete("/memories", params={"user_id": scope})
        step("probe data deleted", d.status_code in (200, 202, 204), f"{d.status_code}")
    print(f"{'ALL OK' if not failures else str(failures) + ' FAILED'}  (probe scope {scope})")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
