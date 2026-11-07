import json
import logging

from fastapi import APIRouter, HTTPException, Query, Request, Response

from src.ingest.dispatch import dispatch
from src.ingest.payload import parse_messages, parse_statuses, verify_signature
from src.loop.outbound import apply_status

log = logging.getLogger(__name__)
router = APIRouter()


@router.get("/webhook")
async def verify(
    request: Request,
    mode: str = Query("", alias="hub.mode"),
    token: str = Query("", alias="hub.verify_token"),
    challenge: str = Query("", alias="hub.challenge"),
):
    expected = request.app.state.settings.wa_verify_token
    if mode == "subscribe" and expected and token == expected:
        return Response(content=challenge, media_type="text/plain")
    raise HTTPException(status_code=403)


@router.post("/webhook")
async def receive(request: Request):
    raw = await request.body()
    secret = request.app.state.settings.wa_app_secret
    if not verify_signature(secret, raw, request.headers.get("x-hub-signature-256")):
        raise HTTPException(status_code=401, detail="bad signature")
    try:
        body = json.loads(raw)
        messages, statuses = parse_messages(body), parse_statuses(body)
    except ValueError:
        raise HTTPException(status_code=400, detail="bad json")
    for st in statuses:
        try:
            await apply_status(request.app, st)
        except Exception:
            log.exception("status handling failed for %s", st.msg_id)
    for msg in messages:
        try:
            await dispatch(request.app, msg)
        except Exception:
            # Always 200 so Meta doesn't retry-storm; dedupe is by source_id anyway.
            log.exception("dispatch failed for %s", msg.wamid)
    return {"ok": True}
