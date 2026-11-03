import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response

from src.bridge.continuum import ContinuumClient
from src.bridge.outbox import stats as outbox_stats
from src.config import load_settings
from src.db import migrate, open_pool
from src.ingest.webhook import router
from src.loop.llm import LLM
from src.loop.pay import router as pay_router
from src.loop.scheduler import run_forever
from src.loop.whatsapp import WhatsAppClient


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = load_settings()
    app.state.settings = s
    app.state.pool = await open_pool(s.database_url)
    await migrate(app.state.pool)
    app.state.continuum = ContinuumClient(s.continuum_url, s.continuum_api_key)
    app.state.wa = WhatsAppClient(s.wa_phone_number_id, s.wa_access_token)
    app.state.llm = LLM(s.llm_base_url, s.llm_api_key, s.llm_model, s.llm_reasoning_model)
    app.state.bg = set()
    ticker = asyncio.create_task(run_forever(app))
    yield
    ticker.cancel()
    await app.state.llm.aclose()
    await app.state.wa.aclose()
    await app.state.continuum.aclose()
    await app.state.pool.close()


app = FastAPI(title="Smara Munim", lifespan=lifespan)
app.include_router(router)
app.include_router(pay_router)


STALE_OUTBOX_S = 1800


@app.get("/healthz")
async def healthz(response: Response):
    """200 when Postgres answers and Continuum delivery is keeping up; else 503 (for an uptime monitor)."""
    try:
        ob = await outbox_stats(app.state.pool)
    except Exception:
        response.status_code = 503
        return {"ok": False, "db": False}
    ok = ob["dead"] == 0 and ob["oldest_pending_s"] < STALE_OUTBOX_S
    if not ok:
        response.status_code = 503
    return {"ok": ok, "db": True, "outbox": ob}
