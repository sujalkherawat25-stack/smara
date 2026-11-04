"""Load + reliability test: many shops talking to the agent at once.

    python -m evals.load_test --db postgresql://.../munim_eval_1 --shops 40 --msgs 3 --mode mock
    python -m evals.load_test --db ... --shops 6 --msgs 2 --mode real --model gemma4   (uses LLM_BASE_URL/LLM_API_KEY)

mock: the real LLM client (retries, pacing, fallback) talks to a fake provider that injects latency, 429s and 5xx.
Checks: every owner message gets exactly one reply, no session stuck, no cross-shop leakage, latency percentiles.
"""
import argparse
import asyncio
import json
import os
import random
import selectors
import statistics
import sys
import tempfile
import time
from datetime import date, timedelta
from types import SimpleNamespace

import httpx

from evals.agent_harness import WA, Mem
from src import tenants
from src.box.sandbox import Sandbox
from src.config import Settings
from src.db import migrate, open_pool
from src.ingest.dispatch import dispatch
from src.ingest.importer import import_rows
from src.ingest.payload import InboundMessage
from src.ingest.sheet_mapper import SheetRow
from src.loop.llm import LLM


def fake_provider(p429: float, p500: float, lat: tuple[float, float]):
    state = {"n": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(random.uniform(*lat))
        r = random.random()
        if r < p429:
            return httpx.Response(429, headers={"retry-after": "1"}, json={"error": "rate"})
        if r < p429 + p500:
            return httpx.Response(503, json={"error": "down"})
        body = json.loads(request.content)
        msgs = body["messages"]
        if msgs[-1]["role"] == "tool":
            return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "Total baaki Rs 100."}}]})
        call = {"id": f"c{state['n']}", "type": "function", "function": {
            "name": "query_ledger_sql", "arguments": json.dumps({"sql": "select count(*) n, sum(amount_paise) s from obligations"})}}
        state["n"] += 1
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "", "tool_calls": [call]}}]})
    return httpx.MockTransport(handler)


async def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--shops", type=int, default=20)
    ap.add_argument("--msgs", type=int, default=3)
    ap.add_argument("--mode", choices=["mock", "real"], default="mock")
    ap.add_argument("--model", default="gemma4")
    ap.add_argument("--p429", type=float, default=0.2)
    ap.add_argument("--p500", type=float, default=0.03)
    ap.add_argument("--rpm", type=float, default=0)
    ap.add_argument("--fallback", default="")
    a = ap.parse_args()
    pool = await open_pool(a.db)
    async with pool.connection() as c:
        await c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
    await migrate(pool)
    T = date.today()
    owners = {}
    for i in range(a.shops):
        phone = f"91900{i:07d}"
        t = await tenants.create(pool, name=f"Shop {i}", owner_phone=phone)
        await import_rows(pool, t.id, [SheetRow(f"Cust{i}", f"91981{i:07d}", f"INV-{i}", (i + 1) * 100_00, T - timedelta(days=3))])
        owners[phone] = t.id
    async with pool.connection() as c:
        await c.execute("update tenants set owner_last_inbound_at = now()")
    if a.mode == "mock":
        llm = LLM("https://fake", "k", "m", agent_model="m", max_concurrency=8, max_backoff_s=3, agent_rpm=a.rpm,
                  client=httpx.AsyncClient(base_url="https://fake", transport=fake_provider(a.p429, a.p500, (0.2, 1.0))))
    else:
        llm = LLM(os.environ["LLM_BASE_URL"], os.environ["LLM_API_KEY"], a.model, agent_model=a.model, agent_rpm=a.rpm,
                  fallback_model=a.fallback)
    wa = WA()
    app = SimpleNamespace(state=SimpleNamespace(pool=pool, settings=Settings(), wa=wa, tg=None, bg=set(), continuum=Mem(),
                                                browsers=None, llm=llm, sandbox=Sandbox(tempfile.mkdtemp(), "local"),
                                                agent_background=False))
    lat: list[float] = []
    fails = 0

    async def shop(phone: str):
        nonlocal fails
        for k in range(a.msgs):
            t0 = time.monotonic()
            try:
                await dispatch(app, InboundMessage(wamid=f"{phone}-{k}", sender=phone, kind="text",
                                                   text="Cust ka kitna baaki hai?"))
            except Exception as e:
                fails += 1
                print("dispatch error", type(e).__name__, str(e)[:100])
            lat.append(time.monotonic() - t0)
            await asyncio.sleep(random.uniform(0, 0.5))

    async def watch():
        while True:
            await asyncio.sleep(3)
            st = pool.get_stats()
            print(f"  pool size={st.get('pool_size')} avail={st.get('pool_available')} waiting={st.get('requests_waiting')}", flush=True)
    w = asyncio.create_task(watch())
    t0 = time.monotonic()
    await asyncio.gather(*[shop(p) for p in owners])
    wall = time.monotonic() - t0
    expected = a.shops * a.msgs
    async with pool.connection() as c:
        st = await (await c.execute("select status, count(*) n from agent_sessions group by status")).fetchall()
    replies = {p: [m for m in wa.to(p) if m.get("type") == "text"] for p in owners}
    missing = [p for p, r in replies.items() if len(r) < a.msgs]
    errors = [p for p, r in replies.items() if any("Sorry" in m["text"]["body"] or "stopped" in m["text"]["body"] for m in r)]
    lat.sort()
    pct = lambda q: lat[min(len(lat) - 1, int(len(lat) * q))]
    print(f"mode={a.mode} shops={a.shops} msgs/shop={a.msgs} total={expected} wall={wall:.1f}s "
          f"throughput={expected / wall:.1f} msg/s")
    print(f"latency p50={pct(.5):.1f}s p95={pct(.95):.1f}s max={lat[-1]:.1f}s mean={statistics.mean(lat):.1f}s")
    print(f"sessions={ {r['status']: r['n'] for r in st} } dispatch_errors={fails} shops_missing_reply={len(missing)} "
          f"shops_with_error_reply={len(errors)} llm_stats={llm.stats}")
    ok = fails == 0 and not missing and not errors and all(r["status"] == "DONE" for r in st)
    print("RESULT:", "PASS" if ok else "FAIL")
    await llm.aclose()
    await pool.close()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    asyncio.run(main(), loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))
