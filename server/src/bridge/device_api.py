"""HTTP side of the Smara Browser Bridge (the PC connects out to these)."""
import asyncio
import logging

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from src.bridge import device
from src.ingest.tally_sync import SyncBody, apply_sync

log = logging.getLogger(__name__)

router = APIRouter(prefix="/bridge")


class PairBody(BaseModel):
    code: str = Field(min_length=8, max_length=12)
    name: str = Field(default="PC", max_length=60)


class LoginStartBody(BaseModel):
    name: str = Field(default="PC", max_length=60)


class LoginPollBody(BaseModel):
    device_secret: str = Field(min_length=10, max_length=100)


class ResultBody(BaseModel):
    job_id: str
    result: dict


class OwnerPairBody(BaseModel):
    code: str = Field(min_length=8, max_length=12)
    name: str = Field(default="PC", max_length=60)


class DecisionBody(BaseModel):
    choice: str = Field(pattern="^(approve|site|reject)$")


async def _device(request: Request, authorization: str | None) -> dict:
    token = (authorization or "").removeprefix("Bearer ").strip()
    dev = await device.device_for_token(request.app.state.pool, token) if token else None
    if dev is None:
        raise HTTPException(status_code=401, detail="unknown or revoked device")
    return dev


async def _owner(request: Request, authorization: str | None) -> dict:
    """The owner's Desktop approvals token. A browser helper's device token is not accepted here."""
    from src.bridge import approvals
    token = (authorization or "").removeprefix("Bearer ").strip()
    owner = await approvals.owner_for_token(request.app.state.pool, token) if token else None
    if owner is None:
        raise HTTPException(status_code=401, detail="unknown or revoked approvals token")
    return owner


@router.post("/approvals/pair")
async def approvals_pair(body: OwnerPairBody, request: Request):
    from src.bridge import approvals
    done = await approvals.complete_pairing(request.app.state.pool, body.code, body.name)
    if done is None:
        raise HTTPException(status_code=400, detail="that code is wrong, used, or expired; send 'desktop approvals' to Smara for a new one")
    return {"owner_token": done["token"], "shop": done["shop"]}


@router.get("/approvals")
async def approvals_list(request: Request, authorization: str | None = Header(default=None)):
    """Steps on the owner's PC that wait for the owner (browser, PC folder, hand-offs), newest first."""
    from src.bridge import approvals
    owner = await _owner(request, authorization)
    return {"approvals": await approvals.pending(request.app.state.pool, str(owner["tenant_id"]))}


class MemoryNote(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class MemoryQuery(BaseModel):
    query: str = Field(min_length=3, max_length=500)


class MemoryToggle(BaseModel):
    enabled: bool


async def _sharing_on(request: Request, owner: dict) -> None:
    from src.bridge import memory_share
    if not await memory_share.is_enabled(request.app.state.pool, str(owner["tenant_id"])):
        raise HTTPException(status_code=403, detail="memory sharing is off for this shop")


@router.get("/memory")
async def memory_status(request: Request, authorization: str | None = Header(default=None)):
    from src.bridge import memory_share
    owner = await _owner(request, authorization)
    return {"enabled": await memory_share.is_enabled(request.app.state.pool, str(owner["tenant_id"]))}


@router.post("/memory/enable")
async def memory_enable(body: MemoryToggle, request: Request, authorization: str | None = Header(default=None)):
    from src.bridge import memory_share
    owner = await _owner(request, authorization)
    await memory_share.set_enabled(request.app.state.pool, str(owner["tenant_id"]), body.enabled)
    return {"enabled": body.enabled}


@router.post("/memory/notes")
async def memory_note(body: MemoryNote, request: Request, authorization: str | None = Header(default=None)):
    from src.bridge import memory_share
    owner = await _owner(request, authorization)
    await _sharing_on(request, owner)
    return await memory_share.add_note(request.app, str(owner["tenant_id"]), body.text)


@router.post("/memory/search")
async def memory_search(body: MemoryQuery, request: Request, authorization: str | None = Header(default=None)):
    from src.bridge import memory_share
    owner = await _owner(request, authorization)
    await _sharing_on(request, owner)
    try:
        return await memory_share.search(request.app, str(owner["tenant_id"]), body.query)
    except Exception:
        raise HTTPException(status_code=502, detail="memory is unavailable right now")


@router.post("/memory/forget")
async def memory_forget(request: Request, authorization: str | None = Header(default=None)):
    from src.bridge import memory_share
    owner = await _owner(request, authorization)
    await memory_share.forget(request.app, str(owner["tenant_id"]))
    return {"ok": True}


@router.post("/approvals/{sid}")
async def approvals_decide(sid: str, body: DecisionBody, request: Request, authorization: str | None = Header(default=None)):
    from src.bridge import approvals
    owner = await _owner(request, authorization)
    if not approvals.rate_ok(str(owner["id"])):
        raise HTTPException(status_code=429, detail="too many decisions in a short time; wait a few minutes")
    outcome = await approvals.decide(request.app, str(owner["tenant_id"]), sid, body.choice,
                                     background=getattr(request.app.state, "agent_background", True))
    if outcome == "already":
        raise HTTPException(status_code=409, detail="already handled")
    if outcome == "not_here":
        raise HTTPException(status_code=403, detail="this approval is answered in chat only")
    return {"ok": True}


@router.post("/login/start")
async def login_start(body: LoginStartBody, request: Request):
    """Smara Desktop asks to sign in. Nothing is granted until the owner confirms the code in their own chat."""
    from urllib.parse import quote
    from src.bridge import login
    ip = request.client.host if request.client else "?"
    if not login.start_ok(ip):
        raise HTTPException(status_code=429, detail="too many sign-in attempts; wait a few minutes")
    started = await login.start(request.app.state.pool, body.name)
    if started is None:
        raise HTTPException(status_code=503, detail="sign-in is busy; try again in a minute")
    code, links = started["code"], {}
    try:
        links["telegram"] = f"https://t.me/{await request.app.state.tg.username()}?start=login_{code}"
    except Exception:
        pass
    number = getattr(getattr(request.app.state, "settings", None), "wa_display_number", "")
    if number:
        links["whatsapp"] = f"https://wa.me/{number}?text={quote('login ' + code)}"
    return {"device_secret": started["secret"], "code": code, "expires_in": started["expires_in"], "interval": login.POLL_S,
            "message": f"login {code}", "links": links}


@router.post("/logout")
async def logout(request: Request, authorization: str | None = Header(default=None)):
    from src.bridge import login
    owner = await _owner(request, authorization)
    await login.logout(request.app.state.pool, owner)
    return {"ok": True}


@router.post("/login/poll")
async def login_poll(body: LoginPollBody, request: Request):
    from src.bridge import login
    return await login.poll(request.app.state.pool, body.device_secret)


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


@router.get("/overview")
async def overview(request: Request, authorization: str | None = Header(default=None)):
    """The paired shop's numbers for Smara Desktop: dues, what waits for the owner, channels, last Tally sync."""
    from src.bridge.overview import shop_overview
    dev = await _device(request, authorization)
    data = await shop_overview(request.app, str(dev["tenant_id"]))
    if data is None:
        raise HTTPException(status_code=404, detail="this shop no longer exists")
    return {**data, "device": dev["name"]}


@router.post("/sync/tally")
async def sync_tally(body: SyncBody, request: Request, authorization: str | None = Header(default=None)):
    """The Desktop app posts the shop's open receivables and payables from Tally; nothing is sent to anyone by this."""
    dev = await _device(request, authorization)
    pool, tenant_id = request.app.state.pool, str(dev["tenant_id"])
    result = await apply_sync(pool, tenant_id, str(dev.get("id") or ""), body)
    if result.get("closed") or result.get("close_skipped"):
        await _tell_owner_about_sync(request.app, tenant_id, result)
    return result


async def _tell_owner_about_sync(app, tenant_id: str, result: dict) -> None:
    """Closed invoices are a change the owner should see; a refused close is one they must check."""
    from src import tenants
    from src.loop.owner_channel import send_to_owner
    from src.loop.whatsapp import text_message
    tenant = await tenants.by_id(app.state.pool, tenant_id)
    if tenant is None:
        return
    lines = []
    if result.get("closed"):
        names = ", ".join(result.get("closed_parties") or [])
        lines.append(f"Tally sync: {result['closed']} bill paid ho gaye (Tally mein ab baaki list mein nahi): {names}.")
    if result.get("close_skipped"):
        lines.append(f"Dhyan dijiye: {result['close_skipped']}")
    try:
        await send_to_owner(app, tenant, text_message(tenant.owner_phone, "\n".join(lines)), kind="owner_text")
    except Exception:
        log.exception("could not tell the owner about a Tally sync for %s", tenant_id)
