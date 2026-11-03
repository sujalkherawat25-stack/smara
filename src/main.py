import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.bridge.continuum import ContinuumClient
from src.config import load_settings
from src.db import migrate, open_pool
from src.ingest.webhook import router
from src.loop.llm import LLM
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


@app.get("/healthz")
async def healthz():
    return {"ok": True}
