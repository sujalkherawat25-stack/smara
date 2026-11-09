import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response

from src.bridge.continuum import ContinuumClient
from src.bridge.outbox import stats as outbox_stats
from src.config import load_settings
from src.db import migrate, open_pool
from src.ingest.connect import router as connect_router
from src.ingest.wa_link_connect import router as wa_link_router
from src.ingest.legal import router as legal_router
from src.ingest.connect import token_for_number
from src.ingest.telegram import TelegramClient
from src.ingest.telegram import router as telegram_router
from src.ingest.webhook import router
from src.bridge.device import BrowserRouter
from src.bridge.device_api import router as device_router
from src.box.browser_session import BrowserSessions
from src.box.sandbox import Sandbox
from src.loop.llm import LLM
from src.loop.pay import router as pay_router
from src.loop.scheduler import run_forever
from src.loop.whatsapp import WhatsAppClient
from src.loop.wa_link import SidecarClient


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = load_settings()
    app.state.settings = s
    app.state.pool = await open_pool(s.database_url)
    await migrate(app.state.pool)
    app.state.continuum = ContinuumClient(s.continuum_url, s.continuum_api_key)
    app.state.wa = WhatsAppClient(s.wa_phone_number_id, s.wa_access_token,
                                  token_for=lambda n: token_for_number(app.state.pool, n))   # shops' own numbers
    # the owner's own SIM through the Baileys sidecar (unofficial). Off unless WA_LINK_URL is set.
    app.state.wa_link = SidecarClient(s.wa_link_url, s.wa_link_token) if s.wa_link_url and s.wa_link_token else None
    app.state.llm = LLM(s.llm_base_url, s.llm_api_key, s.llm_model, s.llm_reasoning_model,
                        agent_model=s.llm_agent_model, max_concurrency=s.llm_max_concurrency,
                        agent_rpm=s.llm_agent_rpm, fallback_model=s.llm_fallback_model,
                        api_keys=s.llm_keys())
    app.state.bg = set()
    app.state.browsers = BrowserRouter(app.state.pool, BrowserSessions())   # the owner's own device first, else the server browser
    app.state.sandbox = Sandbox(s.sandbox_jobs_dir, s.sandbox_mode)
    app.state.tg = TelegramClient(s.telegram_bot_token) if s.telegram_bot_token else None
    ticker = asyncio.create_task(run_forever(app))
    yield
    ticker.cancel()
    await app.state.browsers.close_all()
    if app.state.tg:
        await app.state.tg.aclose()
    await app.state.llm.aclose()
    await app.state.wa.aclose()
    if app.state.wa_link:
        await app.state.wa_link.aclose()
    await app.state.continuum.aclose()
    await app.state.pool.close()


def _vault_ready() -> bool:
    from src.vault import master_keys
    try:
        return bool(master_keys())
    except Exception:
        return False


app = FastAPI(title="Smara", lifespan=lifespan)
app.include_router(router)
app.include_router(device_router)
app.include_router(pay_router)
app.include_router(connect_router)
app.include_router(wa_link_router)
app.include_router(legal_router)
app.include_router(telegram_router)


STALE_OUTBOX_S = 1800


@app.get("/healthz")
async def healthz(response: Response):
    """200 when Postgres answers and Continuum delivery is keeping up; else 503 (for an uptime monitor)."""
    try:
        ob = await outbox_stats(app.state.pool)
    except Exception:
        response.status_code = 503
        return {"ok": False, "db": False}
    st = app.state
    sandbox_ok = st.sandbox.healthy() if getattr(st, "sandbox", None) else False
    ok = ob["dead"] == 0 and ob["oldest_pending_s"] < STALE_OUTBOX_S and sandbox_ok
    if not ok:
        response.status_code = 503
    from src import clock
    async with app.state.pool.connection() as conn:
        ops = await (await conn.execute(
            """SELECT (SELECT count(*) FROM agent_tasks WHERE status = 'PENDING') tasks_pending,
                      (SELECT count(*) FROM outbound_messages WHERE status = 'held') cards_held,
                      (SELECT count(*) FROM agent_sessions WHERE status = 'FAILED' AND updated_at > now() - interval '1 hour') failed_sessions_1h""")).fetchone()
    return {"ok": ok, "db": True, "outbox": ob, "sandbox": sandbox_ok, "continuum": await _continuum_probe(),
            "ops": {**ops, "quiet_hours_now": clock.is_quiet(st.settings)},
            "agent_model": st.llm.agent_model if getattr(st, "llm", None) and st.llm.enabled else None,
            "llm": getattr(getattr(st, "llm", None), "stats", None),
            "vault": _vault_ready(),
            "wa_link": await _wa_link_summary()}


async def _wa_link_summary() -> dict:
    """Shops with their own SIM link, by state (counts only)."""
    from src.loop import wa_link
    return await wa_link.summary(app.state.pool)


_probe = {"at": 0.0, "ok": None, "ms": None}


async def _continuum_probe() -> dict:
    """Read-only search against a probe scope, at most once every 5 minutes."""
    import time
    if time.monotonic() - _probe["at"] > 300 and getattr(app.state, "continuum", None):
        started = time.monotonic()
        try:
            r = await app.state.continuum._http.post("/memories/search",
                                                     json={"user_id": "munim_health", "query": "ping", "top_k": 1})
            _probe.update(ok=r.status_code == 200)
        except Exception:
            _probe.update(ok=False)
        _probe.update(at=time.monotonic(), ms=round((time.monotonic() - started) * 1000))
    return {"ok": _probe["ok"], "latency_ms": _probe["ms"]}
