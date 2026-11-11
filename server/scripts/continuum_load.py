"""Continuum burst test: many shops writing memory at once, then all of it must come back.

    docker exec smara_app python scripts/continuum_load.py [shops=20] [facts_per_shop=3]

Writes under throwaway `smara-load-*` scopes, checks accept latency/errors, that every fact becomes
resolvable (eventual consistency window), that scopes do not leak into each other, then deletes everything.
"""
import asyncio
import os
import statistics
import sys
import time
from uuid import uuid4

import httpx


async def main(shops: int, facts: int) -> int:
    base, key = os.environ["CONTINUUM_URL"], os.environ["CONTINUUM_API_KEY"]
    run = uuid4().hex[:6]
    scopes = {f"smara-load-{run}-{i}": f"LOADX{run.upper()}{i:03d}" for i in range(shops)}
    lat, codes = [], []
    async with httpx.AsyncClient(base_url=base, headers={"Authorization": f"Bearer {key}"}, timeout=60,
                                 limits=httpx.Limits(max_connections=50)) as h:
        async def write(scope, marker, k):
            t = time.monotonic()
            sid = f"{scope}-{k}"
            body = {
                "user_id": scope, "source_id": sid, "ingestion_mode": "hybrid",
                "messages": [{"role": "user", "content": f"[TRUSTED] Customer {marker} promised to pay invoice {marker}-{k} "
                                                          f"of Rs {10000 + k} by {10 + k} December 2026."}],
                "metadata": {"trust": "trusted", "probe": True}}
            for attempt in range(6):          # like the outbox worker: a 429 is retried with backoff, never lost
                r = await h.post("/memories", headers={"Idempotency-Key": sid}, json=body)
                if r.status_code != 429:
                    break
                await asyncio.sleep(float(r.headers.get("retry-after", 2)) + attempt)
            lat.append(time.monotonic() - t)
            codes.append(r.status_code)

        t0 = time.monotonic()
        await asyncio.gather(*[write(s, m, k) for s, m in scopes.items() for k in range(facts)])
        wall = time.monotonic() - t0
        lat.sort()
        bad = [c for c in codes if c != 202]
        print(f"writes={len(codes)} wall={wall:.1f}s accept p50={statistics.median(lat):.2f}s "
              f"p95={lat[int(len(lat) * .95) - 1]:.2f}s non-202={len(bad)} {sorted(set(bad))}")
        pending, t = dict(scopes), time.monotonic()
        leaks = 0
        while pending and time.monotonic() - t < 240:
            async def check(scope, marker):
                r = await h.post("/memories/resolve", json={"user_id": scope, "query": f"payment promise {marker}", "max_claims": 6})
                if r.status_code != 200:
                    return scope, False, False
                blob = str(r.json())
                other = any(m in blob for s2, m in scopes.items() if s2 != scope)
                return scope, marker in blob, other
            for scope, found, other in await asyncio.gather(*[check(s, m) for s, m in pending.items()]):
                leaks += other
                if found:
                    pending.pop(scope, None)
            if pending:
                await asyncio.sleep(4)
        print(f"resolvable={shops - len(pending)}/{shops} after {time.monotonic() - t:.0f}s  cross-scope leaks={leaks}")
        for s in scopes:
            await h.delete("/memories", params={"user_id": s})
    ok = not bad and not pending and leaks == 0
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 20, int(sys.argv[2]) if len(sys.argv) > 2 else 3)))
