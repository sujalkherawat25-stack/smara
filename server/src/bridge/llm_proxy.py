"""Smara's own model for Smara Desktop: an OpenAI-compatible endpoint the PC calls with its owner token.

    POST /bridge/llm/v1/chat/completions      Authorization: Bearer sma_...   (the Desktop approvals token)

The owner does not need an API key: Smara Desktop's default model profile points here after sign-in, and their own keys
(BYOK) stay available under Advanced. The server picks the model (the same one the chat uses), so the request's "model"
is ignored. Usage is recorded in llm_usage like any chat run, so it shares the owner's one daily token budget
(OWNER_DAILY_TOKENS), and one PC can have only a couple of calls in flight so a looping run cannot starve the shops' chats.
Streaming requests are answered with the whole reply as one event (enough for every OpenAI-compatible client)."""
import json
import logging
import time
import uuid

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from src import limits, tenants
from src.bridge import approvals

log = logging.getLogger(__name__)
router = APIRouter(prefix="/bridge/llm/v1")

MAX_BODY = 4_000_000          # bytes; a desktop run with files inlined stays well under this
IN_FLIGHT = 2                 # calls one shop may have running at the same time
MODEL_NAME = "smara"


def _error(status: int, message: str, kind: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"message": message, "type": kind}})


@router.post("/chat/completions")
async def chat_completions(request: Request, authorization: str | None = Header(default=None)):
    app = request.app
    token = (authorization or "").removeprefix("Bearer ").strip()
    owner = await approvals.owner_for_token(app.state.pool, token) if token else None
    if owner is None:
        return _error(401, "Sign in to Smara in Smara Desktop to use Smara's model.", "authentication_error")
    llm = getattr(app.state, "llm", None)
    if llm is None or not getattr(llm, "enabled", False):
        return _error(503, "Smara's model is not available right now.", "server_error")
    raw = await request.body()
    if len(raw) > MAX_BODY:
        return _error(413, "That request is too large for Smara's model.", "invalid_request_error")
    try:
        body = json.loads(raw)
        messages = body["messages"]
        assert isinstance(messages, list) and messages
    except (ValueError, KeyError, AssertionError, TypeError):
        return _error(400, "Send an OpenAI-style body with a non-empty 'messages' list.", "invalid_request_error")
    tools = body.get("tools") if isinstance(body.get("tools"), list) else []
    tid = str(owner["tenant_id"])
    account = await tenants.by_id(app.state.pool, tid)
    if account is not None and not account.limits_exempt:           # the same allowance as chat, a raised one included
        used = (await limits.usage(app.state.pool, tid))["tokens"]
        if used >= (limit := limits.allowance(app.state.settings, account)["tokens"]):
            await limits.alert_team(app, account, limits.Hit("tokens", limit, used,
                                                            await limits.free_at(app.state.pool, tid, "tokens", used, limit)))
            return _error(429, "Today's Smara usage limit is reached (shared with your WhatsApp/Telegram chat). It resets over "
                               "the next 24 hours, or add your own API key under Settings > Models > Advanced.", "rate_limit_error")
    busy = app.state.__dict__.setdefault("desktop_llm_busy", {})
    if busy.get(tid, 0) >= IN_FLIGHT:
        return _error(429, "Smara is already answering two requests from this PC; try again in a moment.", "rate_limit_error")
    busy[tid] = busy.get(tid, 0) + 1
    try:
        msg = await llm.chat(messages, tools)
    except Exception as err:
        log.warning("desktop model call failed for %s: %s", tid[:8], type(err).__name__)
        return _error(502, "Smara's model did not answer; try again.", "server_error")
    finally:
        busy[tid] -= 1
    usage = msg.pop("_usage", {}) or {}
    try:
        async with app.state.pool.connection() as conn:
            await conn.execute(
                """INSERT INTO llm_usage (tenant_id, session_id, model, prompt_tokens, completion_tokens, total_tokens)
                   VALUES (%s, NULL, %s, %s, %s, %s)""",
                (tid, "desktop:" + str(usage.get("model") or MODEL_NAME), usage.get("prompt", 0), usage.get("completion", 0),
                 usage.get("total", 0)))
    except Exception:
        log.warning("desktop model usage not recorded for %s", tid[:8], exc_info=True)
    message = {"role": "assistant", "content": msg.get("content") or ""}
    if msg.get("tool_calls"):
        message["tool_calls"] = msg["tool_calls"]
    finish = "tool_calls" if msg.get("tool_calls") else "stop"
    out_usage = {"prompt_tokens": usage.get("prompt", 0), "completion_tokens": usage.get("completion", 0),
                 "total_tokens": usage.get("total", 0)}
    rid, created = "chatcmpl-" + uuid.uuid4().hex[:24], int(time.time())
    if body.get("stream"):
        delta = dict(message)
        if delta.get("tool_calls"):
            delta["tool_calls"] = [{**c, "index": i} for i, c in enumerate(delta["tool_calls"])]
        chunk = {"id": rid, "object": "chat.completion.chunk", "created": created, "model": MODEL_NAME,
                 "choices": [{"index": 0, "delta": delta, "finish_reason": finish}], "usage": out_usage}

        async def events():
            yield f"data: {json.dumps(chunk)}\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(events(), media_type="text/event-stream")
    return {"id": rid, "object": "chat.completion", "created": created, "model": MODEL_NAME,
            "choices": [{"index": 0, "message": message, "finish_reason": finish}], "usage": out_usage}


@router.get("/models")
async def models(request: Request, authorization: str | None = Header(default=None)):
    token = (authorization or "").removeprefix("Bearer ").strip()
    if await approvals.owner_for_token(request.app.state.pool, token) is None:
        raise HTTPException(status_code=401)
    return {"object": "list", "data": [{"id": MODEL_NAME, "object": "model", "owned_by": "smara"}]}
