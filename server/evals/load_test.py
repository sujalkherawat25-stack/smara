"""Load test: N shops using the real app code at the same time, against a scratch Postgres (name must end _eval).

Everything outside the model is real: the Telegram update handler, owner flow, agent loop, tools, Postgres pool, the scheduler.
Telegram, WhatsApp, Continuum (memory) and browsers are fakes that answer after a short delay. The model is either a fake with a
fixed delay (infrastructure limits) or the real provider (rate limits, queueing). It measures what an owner would feel: seconds
from sending a message to the first answer, plus pool waits, event-loop stalls and logged errors.

    python -m evals.load_test --db postgresql://.../smara_eval --scenario burst --shops 30 --llm fake --latency 2
    python -m evals.load_test --db ... --scenario burst --shops 10 --llm real --model glm5.3-flash
    python -m evals.load_test --db ... --scenario morning --shops 30 --llm fake --latency 3

Scenarios: burst (every shop asks one question at the same moment), steady (messages at random times over --window seconds),
morning (every shop has due reminders when quiet hours end: one scheduler tick plus the digests, while owners keep chatting).
"""
import argparse
import asyncio
import json
import logging
import os
import random
import selectors
import statistics
import sys
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from src import clock, health_watch, tenants
from src.box.sandbox import Sandbox
from src.config import Settings, load_settings
from src.db import migrate, open_pool
from src.ingest import telegram
from src.ingest.importer import import_rows
from src.ingest.sheet_mapper import SheetRow
from src.loop.llm import LLM
from src.loop import scheduler

TODAY = date.today()
CUSTOMERS = [("Agarwal Agencies", 220000, 55), ("Singh Electronics", 98750, 31), ("Chopra Sons", 42000, 42),
             ("Gupta Traders", 12000, 46), ("Gupta Traders", 7800, 20), ("Jain Hardware", 31000, 8),
             ("Ravi Kirana Store", 16575, 6), ("Dutta Agencies", 6500, -9), ("Pandey Sweets", 15300, -14),
             ("Mehta Kirana", 3300, -30)]
QUESTIONS = ["Aaj kisko call karun? Sabse zyada overdue wale top 3 batao.", "Business ka summary do: kul kitna baaki hai aur kitna overdue?",
             "Gupta Traders ka kitna baaki hai?", "45 din se zyada purana kitna paisa baaki hai?"]


class FakeTG:
    def __init__(self, delay: float = 0.05):
        self.delay, self.sent, self.actions = delay, [], 0

    async def send(self, payload):
        await asyncio.sleep(self.delay)
        self.sent.append((time.monotonic(), payload["chat_id"], str(payload.get("text", ""))))

    async def typing(self, chat_id):
        self.actions += 1

    async def answer(self, *a, **k):
        return None

    async def send_document(self, chat_id, data, filename, caption=""):
        await asyncio.sleep(self.delay)
        self.sent.append((time.monotonic(), chat_id, f"[file {filename}]"))


class FakeWA:
    def __init__(self):
        self.sent = []

    async def send(self, payload, **kw):
        await asyncio.sleep(0.05)
        self.sent.append(payload)
        return f"wamid.{len(self.sent)}"

    async def upload_media(self, *a, **k):
        return "media.1"


class FakeMemory:
    """Continuum answers in about 150 ms in production."""
    async def resolve(self, **kw):
        await asyncio.sleep(0.15)
        return {"resolution": {"state_card": "No relevant state"}}

    async def write(self, *a, **k):
        await asyncio.sleep(0.1)
        return {}

    async def ingest(self, *a, **k):
        return {}


class FakeBrowsers:
    def alive(self, sid):
        return False

    def page(self, sid):
        return {}

    async def step(self, *a, **k):
        return {"error": "no browser in the load test"}

    async def close(self, sid):
        return None

    async def close_all(self):
        return None


class FakeLLM:
    """A model that answers after `latency` seconds: first a ledger query, then a short answer. Counts calls."""
    enabled = True

    def __init__(self, latency: float, jitter: float = 0.3):
        self.latency, self.jitter, self.stats = latency, jitter, {"calls": 0, "retries": 0, "fallbacks": 0, "rate_limited": 0}
        self.last_usage = {}

    async def chat(self, messages, tools, allow_fallback=True):
        self.stats["calls"] += 1
        await asyncio.sleep(self.latency * (1 + random.uniform(-self.jitter, self.jitter)))
        usage = {"model": "fake", "prompt": 9000, "completion": 120, "total": 9120}
        if messages and messages[-1]["role"] == "tool":
            return {"role": "assistant", "content": "Top 3: Agarwal Agencies ₹2,20,000, Singh Electronics ₹98,750, Chopra Sons ₹42,000.",
                    "_usage": usage}
        if any(m["role"] == "user" and "reminder #" in str(m.get("content")) for m in messages):
            return {"role": "assistant", "content": "ok", "_usage": usage}
        return {"role": "assistant", "content": "", "_usage": usage, "tool_calls": [{
            "id": f"c{self.stats['calls']}", "type": "function", "function": {
                "name": "query_ledger_sql", "arguments": json.dumps({"sql": "select count(*) n from obligations"})}}]}

    async def aclose(self):
        return None


class Capture(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.items: dict[str, int] = {}

    def emit(self, record):
        key = f"{record.levelname} {record.name}: {str(record.msg)[:90]}"
        self.items[key] = self.items.get(key, 0) + 1


async def seed(pool, shops: int, due_per_shop: int, tg_ids: list[int]) -> list:
    async with pool.connection() as c:
        await c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
    await migrate(pool)
    out = []
    for i in range(shops):
        t = await tenants.create(pool, name=f"Load Shop {i:02d}", owner_phone=f"9188{i:08d}", upi_vpa=f"shop{i}@upi")
        async with pool.connection() as c:
            await c.execute("UPDATE tenants SET telegram_chat_id = %s, owner_last_channel = 'telegram' WHERE id = %s", (tg_ids[i], t.id))
        rows = [SheetRow(name, f"9177{i:04d}{j:04d}", f"B-{i}-{j}", int(r * 100), TODAY - timedelta(days=days))
                for j, (name, r, days) in enumerate(CUSTOMERS)]
        await import_rows(pool, t.id, rows)
        out.append(await tenants.by_id(pool, t.id))
    if due_per_shop:
        async with pool.connection() as c:
            await c.execute("""UPDATE obligations SET next_followup_at = now() - interval '1 minute'
                                WHERE id IN (SELECT id FROM (SELECT id, row_number() OVER (PARTITION BY tenant_id ORDER BY amount_paise DESC) rn
                                                               FROM obligations WHERE party_id IN (SELECT id FROM parties WHERE whatsapp_phone IS NOT NULL)) x
                                              WHERE rn <= %s)""", (due_per_shop,))
    return out


def update(chat_id: int, n: int, text: str) -> dict:
    return {"update_id": n, "message": {"message_id": n, "chat": {"id": chat_id, "type": "private"}, "text": text}}


def _rss_mb() -> int:
    try:
        import resource                      # Linux only (the container); absent on Windows
        return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)
    except ImportError:
        return -1


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * p))] if xs else float("nan")


async def monitor(stats: dict, pool, stop: asyncio.Event):
    """Event-loop lag (a blocked loop makes every shop wait) and the most requests ever waiting for a database connection."""
    while not stop.is_set():
        t0 = time.monotonic()
        await asyncio.sleep(0.05)
        stats["lag_max"] = max(stats.get("lag_max", 0.0), time.monotonic() - t0 - 0.05)
        try:
            stats["pool_waiting_max"] = max(stats.get("pool_waiting_max", 0), pool.get_stats().get("requests_waiting", 0))
        except Exception:
            pass


async def drain(app, timeout: float) -> bool:
    """Wait for every background agent run to end."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pending = [t for t in list(app.state.bg) if not t.done()]
        if not pending:
            return True
        await asyncio.wait(pending, timeout=1.0)
    return False


async def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--scenario", choices=["burst", "steady", "morning"], default="burst")
    ap.add_argument("--shops", type=int, default=20)
    ap.add_argument("--llm", choices=["fake", "real"], default="fake")
    ap.add_argument("--latency", type=float, default=2.0, help="fake model seconds per call")
    ap.add_argument("--model", default="")
    ap.add_argument("--rpm", type=float, default=None, help="override LLM_AGENT_RPM for the real model")
    ap.add_argument("--concurrency", type=int, default=None)
    ap.add_argument("--no-fallback", action="store_true")
    ap.add_argument("--burst", type=int, default=None, help="override LLM_AGENT_BURST for the real model")
    ap.add_argument("--window", type=float, default=120.0, help="steady: seconds the messages are spread over")
    ap.add_argument("--per-shop", type=int, default=2, help="steady: messages per shop")
    ap.add_argument("--due", type=int, default=3, help="morning: due reminders per shop")
    ap.add_argument("--timeout", type=float, default=600.0)
    ap.add_argument("--json", default="")
    a = ap.parse_args()
    if not a.db.split("?")[0].endswith("_eval"):
        sys.exit("refusing to run: the database name must end in _eval")
    random.seed(7)
    capture = Capture()
    logging.getLogger().addHandler(capture)
    health_watch.install()
    pool = await open_pool(a.db)
    tg_ids = [700000 + i for i in range(a.shops)]
    shop_list = await seed(pool, a.shops, a.due if a.scenario == "morning" else 0, tg_ids)
    s = load_settings()
    if a.llm == "real":
        llm = LLM(s.llm_base_url, s.llm_api_key, a.model or s.llm_model, s.llm_reasoning_model, agent_model=a.model or s.llm_agent_model,
                  max_concurrency=a.concurrency or s.llm_max_concurrency, agent_rpm=s.llm_agent_rpm if a.rpm is None else a.rpm,
                  fallback_model="" if a.no_fallback else s.llm_fallback_model, api_keys=s.llm_keys(),
                  agent_burst=s.llm_agent_burst if a.burst is None else a.burst, fallback_on_busy=s.llm_fallback_on_busy)
        tokens = {"t": 0}
        real_chat = llm.chat

        async def counted(messages, tools, **kw):
            m = await real_chat(messages, tools, **kw)
            tokens["t"] += int((llm.last_usage or {}).get("total_tokens") or 0)
            return m
        llm.chat = counted
    else:
        llm, tokens = FakeLLM(a.latency), {"t": 0}
    tg = FakeTG()
    settings = Settings(owner_files_dir=tempfile.mkdtemp(prefix="load-files-"), ops_telegram_chat_id=None)
    app = SimpleNamespace(state=SimpleNamespace(pool=pool, settings=settings, wa=FakeWA(), tg=tg, bg=set(), continuum=FakeMemory(),
                                                browsers=FakeBrowsers(), llm=llm, sandbox=Sandbox(tempfile.mkdtemp(prefix="load-jobs-"), "local"),
                                                agent_background=True))
    stats: dict = {}
    stop = asyncio.Event()
    mon = asyncio.create_task(monitor(stats, pool, stop))
    started: dict[int, float] = {}
    t_begin = time.monotonic()
    counter = [1000]

    async def send(i: int, text: str, at: float = 0.0):
        if at:
            await asyncio.sleep(at)
        counter[0] += 1
        started.setdefault(tg_ids[i], time.monotonic())
        await telegram.handle_update(app, update(tg_ids[i], counter[0], text))

    tick_info = {}
    if a.scenario == "burst":
        await asyncio.gather(*(send(i, QUESTIONS[i % len(QUESTIONS)]) for i in range(a.shops)))
    elif a.scenario == "steady":
        jobs = []
        for i in range(a.shops):
            for k in range(a.per_shop):
                jobs.append(send(i, QUESTIONS[(i + k) % len(QUESTIONS)], random.uniform(0, a.window) + k * 0.01))
        await asyncio.gather(*jobs)
    else:   # morning: quiet hours just ended. Reminders, digests and a few owners writing at the same time.
        async def run_tick():
            t0 = time.monotonic()
            tick_info["sent"] = await scheduler.tick(app, datetime.now(timezone.utc))
            tick_info["tick_s"] = round(time.monotonic() - t0, 1)

        async def run_digests():
            t0 = time.monotonic()
            tick_info["digests"] = await scheduler.send_digests(app, datetime.now(timezone.utc).astimezone(clock.IST))
            tick_info["digest_s"] = round(time.monotonic() - t0, 1)
        chat = [send(i, QUESTIONS[i % len(QUESTIONS)], 2.0 + i * 0.5) for i in range(0, a.shops, 3)]
        await asyncio.gather(run_tick(), run_digests(), *chat)
    finished = await drain(app, a.timeout)
    wall = time.monotonic() - t_begin
    stop.set()
    await mon

    first, last = {}, {}
    for ts, chat_id, text in tg.sent:
        if chat_id in started and ts >= started[chat_id] and "Still working" not in text:
            first.setdefault(chat_id, ts - started[chat_id])
            last[chat_id] = ts - started[chat_id]
    lat = [first[c] for c in started if c in first]
    missing = [c for c in started if c not in first]
    async with pool.connection() as c:
        st = await (await c.execute("select status, count(*) n, coalesce(sum(steps),0) steps from agent_sessions group by status")).fetchall()
    ps = pool.get_stats()
    report = {
        "scenario": a.scenario, "shops": a.shops, "llm": a.llm, "latency": a.latency if a.llm == "fake" else None,
        "messages": len(started), "answered": len(lat), "unanswered": len(missing), "drained": finished, "wall_s": round(wall, 1),
        "first_answer_s": {"p50": round(pct(lat, .5), 1), "p90": round(pct(lat, .9), 1), "max": round(max(lat, default=float("nan")), 1)},
        "sessions": {r["status"]: r["n"] for r in st}, "agent_steps": sum(int(r["steps"]) for r in st),
        "llm_stats": getattr(llm, "stats", {}), "tokens": tokens["t"], "typing_calls": tg.actions, "tg_messages": len(tg.sent),
        "event_loop_lag_max_s": round(stats.get("lag_max", 0.0), 2), "pool_waiting_max": stats.get("pool_waiting_max", 0),
        "pool": {k: ps.get(k) for k in ("pool_size", "pool_max", "requests_num", "requests_queued", "requests_wait_ms", "requests_errors")},
        "tick": tick_info, "rss_mb": _rss_mb(),
        "logged": dict(sorted(capture.items.items(), key=lambda kv: -kv[1])[:8]),
        "health_report": health_watch.summary()[:600],
    }
    print(json.dumps(report, indent=1, ensure_ascii=False), flush=True)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=1, ensure_ascii=False)
    await pool.close()


if __name__ == "__main__":
    asyncio.run(main(), loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))
