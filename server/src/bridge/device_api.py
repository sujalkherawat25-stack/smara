"""HTTP side of the Smara Browser Bridge (the PC connects out to these)."""
import asyncio

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from src.bridge import device
from src.ingest.tally_sync import SyncBody, apply_sync

router = APIRouter(prefix="/bridge")


class PairBody(BaseModel):
    code: str = Field(min_length=8, max_length=12)
    name: str = Field(default="PC", max_length=60)


class ResultBody(BaseModel):
    job_id: str
    result: dict


async def _device(request: Request, authorization: str | None) -> dict:
    token = (authorization or "").removeprefix("Bearer ").strip()
    dev = await device.device_for_token(request.app.state.pool, token) if token else None
    if dev is None:
        raise HTTPException(status_code=401, detail="unknown or revoked device")
    return dev


@router.post("/pair")
async def pair(body: PairBody, request: Request):
    done = await device.complete_pairing(request.app.state.pool, body.code, body.name)
    if done is None:
        raise HTTPException(status_code=400, detail="that code is wrong, used, or expired; ask Smara for a new one")
    return {"device_token": done["token"], "shop": done["shop"]}


@router.post("/poll")
async def poll(request: Request, authorization: str | None = Header(default=None), wait: int = device.POLL_S):
    """Long poll: returns a job as soon as one exists, or {} after `wait` seconds (the bridge simply asks again)."""
    dev = await _device(request, authorization)
    deadline = asyncio.get_running_loop().time() + max(0, min(wait, device.POLL_S))
    while True:
        job = await device.claim_job(request.app.state.pool, dev)
        if job:
            return job
        if asyncio.get_running_loop().time() >= deadline:
            return {}
        await asyncio.sleep(0.5)
        await device.device_for_token(request.app.state.pool, (authorization or "").removeprefix("Bearer ").strip())  # keeps last_seen fresh


@router.post("/result")
async def result(body: ResultBody, request: Request, authorization: str | None = Header(default=None)):
    dev = await _device(request, authorization)
    if not await device.finish_job(request.app.state.pool, dev, body.job_id, body.result):
        raise HTTPException(status_code=404, detail="no such job in progress")
    return {"ok": True}


@router.post("/sync/tally")
async def sync_tally(body: SyncBody, request: Request, authorization: str | None = Header(default=None)):
    """The Desktop app posts the shop's open receivables and payables from Tally; nothing is sent to anyone by this."""
    dev = await _device(request, authorization)
    return await apply_sync(request.app.state.pool, str(dev["tenant_id"]), str(dev.get("id") or ""), body)
