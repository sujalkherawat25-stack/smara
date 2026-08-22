from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
import base64
import secrets
from datetime import datetime, timezone, timedelta
from pathlib import Path
from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import httpx
import jwt

from .config import settings
from .models import AccountDeletionRequest, ApprovalDecision, ArtifactView, ChatRequest, ChatResponse, CliDeviceAuthorize, CliPairingExchange, CliPairingStart, EvidenceView, ExecutorComplete, ExecutorFailure, ExecutorHeartbeat, ExecutorPairingCreate, ExecutorPairRequest, IntegrationActionCreate, IntegrationActionDecision, IntegrationConfigure, IntegrationCredentialInput, PushSubscriptionInput, ResearchTaskCreate, ScheduleCreate, ScheduleView, TaskCreate, TaskView, ToolInvokeRequest
from .store import open_task_store
from .agent_runtime import OpenAICompatibleProvider, SmaraAgentRuntime
from . import agent_events, llm_errors
from .syntarus_adapter import SyntarusMemory
from .vault import SecretVault
from . import integration_oauth
from . import push
from .hardening import RedisFixedWindowLimiter
from .observability import configure_sentry
from .tool_registry import ToolContext, ToolError, default_tool_registry

configure_sentry(settings.sentry_dsn)
app = FastAPI(title="Smara Control Plane", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.allowed_origins.split(",") if origin.strip()],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)
store = open_task_store(database_url=settings.database_url, database_path=settings.database_path)
limiter = RedisFixedWindowLimiter(settings.redis_url, settings.rate_limit_per_minute, allow_local_fallback=settings.dev_mode)
source_web_dir = Path(__file__).resolve().parents[2] / "web"
web_dir = source_web_dir if source_web_dir.is_dir() else Path("/app/web")
if web_dir.is_dir():
    app.mount("/app", StaticFiles(directory=web_dir, html=True), name="smara-web")


def _agent_runtime() -> SmaraAgentRuntime:
    """Construct the runtime without importing any MemoryOS implementation."""
    memory = None
    if settings.syntarus_api_key:
        from syntarus import AsyncMemoryClient
        memory = SyntarusMemory(AsyncMemoryClient(settings.syntarus_api_key, base_url=settings.syntarus_base_url))
    return SmaraAgentRuntime(
        OpenAICompatibleProvider(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            model=settings.llm_model,
        ),
        memory=memory,
    )

@app.get("/", include_in_schema=False)
async def web_root():
    if web_dir.is_dir():
        return FileResponse(web_dir / "index.html")
    return {"ok": True}

@app.middleware("http")
async def harden_http(request: Request, call_next):
    if request.url.path.startswith("/v1/"):
        client = request.client.host if request.client else "unknown"
        try:
            allowed = await limiter.allow(client)
        except RuntimeError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=503)
        if not allowed:
            return JSONResponse({"detail": "Rate limit exceeded. Try again shortly."}, status_code=429, headers={"Retry-After": "60"})
    response = await call_next(request)
    response.headers.update({"X-Content-Type-Options": "nosniff", "Referrer-Policy": "strict-origin-when-cross-origin", "Permissions-Policy": "camera=(self), microphone=(self), geolocation=()"})
    # The Control UI is embedded only by ai.syntarus.com. Caddy applies the
    # strict frame-ancestors policy; every API route and non-embedded page
    # remains protected from framing with X-Frame-Options.
    if not request.url.path.startswith("/app/"):
        response.headers["X-Frame-Options"] = "DENY"
    return response

def account_id(
    x_smara_account_id: str | None = Header(default=None),
    x_smara_gateway_timestamp: str | None = Header(default=None),
    x_smara_gateway_signature: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> str:
    """Resolve an account from a development header or trusted production bridge.

    Production supports either a short-lived JWT minted after an existing Smara
    web-session check, or a signed server-to-server gateway assertion.  Neither
    signing secret is ever exposed to a browser.
    """
    if settings.dev_mode:
        if not x_smara_account_id:
            raise HTTPException(401, "X-Smara-Account-Id is required in development.")
        return x_smara_account_id
    # Direct unit calls see FastAPI's Header sentinel for omitted arguments;
    # requests resolved by FastAPI always provide a string or None.
    if isinstance(authorization, str) and authorization:
        if not authorization.startswith("Bearer "):
            raise HTTPException(401, "Invalid authorization scheme.")
        token = authorization.removeprefix("Bearer ")
        if settings.control_bridge_secret:
            try:
                claims = jwt.decode(token, settings.control_bridge_secret, algorithms=["HS256"], audience="smara-control", issuer="ai.syntarus.com", options={"require": ["sub", "exp", "iat", "aud", "iss"]})
                subject = claims.get("sub")
                if isinstance(subject, str) and subject.startswith("acct_"):
                    return subject
            except jwt.InvalidTokenError:
                pass
        if settings.cli_token_secret:
            try:
                claims = jwt.decode(token, settings.cli_token_secret, algorithms=["HS256"], audience="smara-cli", issuer="smara-api", options={"require": ["sub", "exp", "iat", "aud", "iss", "jti"]})
                subject = claims.get("sub")
                if isinstance(subject, str) and subject.startswith("acct_"):
                    return subject
            except jwt.InvalidTokenError:
                pass
        raise HTTPException(401, "Invalid or expired Smara session token.")

    if not settings.gateway_signing_secret:
        raise HTTPException(503, "Production identity gateway is not configured.")
    if not x_smara_account_id:
        raise HTTPException(401, "Authenticated account assertion is required.")
    try:
        timestamp = int(x_smara_gateway_timestamp or "")
    except ValueError:
        raise HTTPException(401, "Invalid gateway timestamp.")
    if abs(time.time() - timestamp) > 300:
        raise HTTPException(401, "Expired gateway assertion.")
    expected = hmac.new(
        settings.gateway_signing_secret.encode(),
        f"{timestamp}.{x_smara_account_id}".encode(), hashlib.sha256,
    ).hexdigest()
    if not x_smara_gateway_signature or not hmac.compare_digest(expected, x_smara_gateway_signature):
        raise HTTPException(401, "Invalid gateway assertion.")
    return x_smara_account_id

def _as_datetime(value: datetime | str) -> datetime:
    return value if isinstance(value, datetime) else datetime.fromisoformat(value)


def view(row: dict) -> TaskView:
    return TaskView(**{
        **row,
        "requires_approval": bool(row["requires_approval"]),
        "created_at": _as_datetime(row["created_at"]),
        "updated_at": _as_datetime(row["updated_at"]),
    })


def evidence_view(row: dict) -> EvidenceView:
    quality_flags = row.get("quality_flags") or []
    if isinstance(quality_flags, str):
        try: quality_flags = json.loads(quality_flags)
        except json.JSONDecodeError: quality_flags = []
    return EvidenceView(**{**row, "retrieved_at": _as_datetime(row["retrieved_at"]) if row.get("retrieved_at") else None, "quality_flags": quality_flags, "agreement_count": int(row.get("agreement_count") or 0)})


def artifact_view(row: dict) -> ArtifactView:
    return ArtifactView(**{**row, "created_at": _as_datetime(row["created_at"])})

def schedule_view(row: dict) -> ScheduleView:
    return ScheduleView(**{
        **row,
        "enabled": bool(row["enabled"]),
        "requires_approval": bool(row["requires_approval"]),
        "next_run_at": _as_datetime(row["next_run_at"]),
        "last_run_at": _as_datetime(row["last_run_at"]) if row.get("last_run_at") else None,
        "created_at": _as_datetime(row["created_at"]),
        "updated_at": _as_datetime(row["updated_at"]),
    })

@app.get("/health")
async def health(): return {"ok": True, "memory_boundary": "syntarus-sdk-only", "auth_mode": "development" if settings.dev_mode else "signed-gateway"}

@app.get("/v1/tools")
async def list_tools(user: str = Depends(account_id)):
    """Return the safe tools currently available to the agent runtime."""
    return {"tools": default_tool_registry().describe()}

@app.post("/v1/tools/{tool_name}")
async def invoke_tool(tool_name: str, body: ToolInvokeRequest, user: str = Depends(account_id)):
    """Run one read-only tool; side effects must use the durable task graph."""
    async with httpx.AsyncClient(timeout=httpx.Timeout(12.0), follow_redirects=False) as client:
        try:
            result = await default_tool_registry(client).invoke(
                tool_name, body.arguments, ToolContext(user, body.workspace_id, client)
            )
        except ToolError as exc:
            raise HTTPException(400, str(exc)) from exc
    return {"ok": result.ok, "content": result.content, "citations": result.citations, "meta": result.meta}

@app.get("/readyz")
async def readyz():
    try:
        with store._connect() as connection:
            connection.execute("SELECT 1")
        return {"ok": True}
    except Exception:
        raise HTTPException(503, "Smara database is not ready.")


@app.post("/v1/cli/device/start")
async def start_cli_pairing(body: CliPairingStart, user: str = Depends(account_id)):
    """Called by an authenticated Web/Memento session to authorize a CLI."""
    return store.create_cli_pairing(user, body.name)


def _issue_cli_token(account_id: str, name: str) -> dict:
    if not settings.cli_token_secret:
        raise HTTPException(503, "CLI authentication is not configured on this Smara deployment.")
    now = int(time.time())
    expires_in = max(1, settings.cli_token_ttl_days) * 86400
    token = jwt.encode({
        "sub": account_id, "name": name, "jti": f"cli_{secrets.token_hex(16)}",
        "iat": now, "exp": now + expires_in,
        "aud": "smara-cli", "iss": "smara-api",
    }, settings.cli_token_secret, algorithm="HS256")
    return {"access_token": token, "token_type": "bearer", "expires_in": expires_in}


@app.get("/v1/cli/device/request")
async def request_cli_device(name: str = Query(default="Smara CLI", min_length=1, max_length=120)):
    """Start a browser-authorized CLI login; the random code is never persisted plaintext."""
    if not settings.cli_token_secret:
        raise HTTPException(503, "CLI authentication is not configured on this Smara deployment.")
    return store.create_cli_device_request(name)


@app.post("/v1/cli/device/authorize")
async def authorize_cli_device(body: CliDeviceAuthorize, user: str = Depends(account_id)):
    """Approve a CLI request from the already authenticated Smara Web session."""
    try:
        result = store.authorize_cli_device(body.device_code, user)
    except KeyError as exc:
        raise HTTPException(409, "CLI request is expired, already approved, or invalid.") from exc
    return {"ok": True, "name": result["name"]}


@app.get("/v1/cli/device/poll")
async def poll_cli_device(device_code: str = Query(..., min_length=20, max_length=220)):
    """Poll a device request without exposing account identity before approval."""
    result = store.poll_cli_device(device_code)
    if result["status"] == "approved":
        return {"status": "approved", **_issue_cli_token(result["account_id"], result["name"])}
    return result


@app.post("/v1/cli/device/exchange")
async def exchange_cli_pairing(body: CliPairingExchange):
    """Exchange a one-time Web-issued code for a scoped CLI bearer token."""
    if not settings.cli_token_secret:
        raise HTTPException(503, "CLI authentication is not configured on this Smara deployment.")
    try:
        pairing = store.consume_cli_pairing(body.code)
    except KeyError as exc:
        raise HTTPException(401, "CLI pairing code is invalid, expired, or already used.") from exc
    return _issue_cli_token(pairing["account_id"], pairing["name"])


@app.post("/v1/chat", response_model=ChatResponse)
async def chat(body: ChatRequest, user: str = Depends(account_id)):
    """Direct chat with bounded read-only tools; writes remain durable tasks."""
    runtime = _agent_runtime()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0), follow_redirects=False) as client:
            turn = await runtime.chat_with_tools(
                account_id=user,
                workspace_id=body.workspace_id,
                message=body.message,
                conversation_id=body.conversation_id,
                http_client=client,
            )
        return ChatResponse(**turn.__dict__)
    except httpx.HTTPStatusError as exc:
        raise HTTPException(502, "The configured Smara model provider rejected this chat request.") from exc
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    finally:
        memory = getattr(runtime, "_memory", None)
        if memory is not None:
            await memory.aclose()


@app.post("/v1/chat/stream")
async def chat_stream(body: ChatRequest, user: str = Depends(account_id)):
    """SSE view of direct chat and bounded read-only tool progress."""
    async def emit():
        started_at = time.perf_counter()
        runtime = _agent_runtime()
        queue: asyncio.Queue[str] = asyncio.Queue()

        def event_hook(event_type: str, payload: dict) -> None:
            if event_type == "agent.tool_requested":
                queue.put_nowait(agent_events.tool_call(str(payload.get("tool", "unknown"))))
            elif event_type == "agent.tool_completed":
                queue.put_nowait(agent_events.tool_result(
                    str(payload.get("tool", "unknown")),
                    ok=bool(payload.get("ok")),
                    preview=str(payload.get("preview", "")),
                ))

        async def run_chat():
            async with httpx.AsyncClient(timeout=httpx.Timeout(20.0), follow_redirects=False) as client:
                return await runtime.chat_with_tools(
                    account_id=user,
                    workspace_id=body.workspace_id,
                    message=body.message,
                    conversation_id=body.conversation_id,
                    http_client=client,
                    event_hook=event_hook,
                )

        task = asyncio.create_task(run_chat())
        try:
            yield agent_events.phase("retrieve")
            yield agent_events.status("Retrieving relevant shared context")
            yield agent_events.phase("reason_act")
            while not task.done():
                try:
                    yield await asyncio.wait_for(queue.get(), timeout=0.1)
                except asyncio.TimeoutError:
                    continue
            while not queue.empty():
                yield queue.get_nowait()
            turn = await task
            yield agent_events.phase("answer")
            yield agent_events.token(turn.message)
            yield agent_events.done(memory_used=turn.memory_used, tools_used=turn.tools_used, total_ms=agent_events.elapsed_ms(started_at))
        except Exception as exc:
            if not task.done():
                task.cancel()
            kind, message = llm_errors.describe(exc, provider=settings.llm_provider)
            yield agent_events.error(message, kind=kind)
        finally:
            memory = getattr(runtime, "_memory", None)
            if memory is not None:
                await memory.aclose()

    return StreamingResponse(
        emit(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )

@app.post("/v1/tasks", response_model=TaskView, status_code=201)
async def create_task(body: TaskCreate, user: str = Depends(account_id)):
    steps = [{"name": step.name, "depends_on": step.depends_on, "executor_kind": step.executor_kind, "required_capability": step.required_capability, "executor_payload": step.executor_payload} for step in body.steps]
    return view(store.create(user, body.workspace_id, body.title, body.objective, body.requires_approval, steps))

def executor_identity(authorization: str | None = Header(default=None), x_smara_executor_id: str | None = Header(default=None)) -> tuple[str, str]:
    if not x_smara_executor_id or not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Executor id and bearer token are required.")
    return x_smara_executor_id, authorization.removeprefix("Bearer ")

@app.post("/v1/executors/pairings")
async def create_executor_pairing(body: ExecutorPairingCreate, user: str = Depends(account_id)):
    return store.create_executor_pairing(user, body.name, body.capabilities)

@app.post("/v1/executors/pair")
async def pair_executor(body: ExecutorPairRequest):
    try: return store.pair_executor(body.code)
    except KeyError: raise HTTPException(400, "Pairing code is invalid, expired, or already used.")

@app.post("/v1/executors/heartbeat")
async def heartbeat_executor(body: ExecutorHeartbeat, identity: tuple[str, str] = Depends(executor_identity)):
    try: return store.heartbeat_executor(*identity, body.capabilities)
    except KeyError: raise HTTPException(401, "Executor credentials are invalid or revoked.")

@app.get("/v1/executors")
async def list_executors(user: str = Depends(account_id)):
    return {"executors": store.executors(user)}

@app.post("/v1/executors/claim")
async def claim_executor(identity: tuple[str, str] = Depends(executor_identity)):
    try: return {"step": store.claim_for_executor(*identity)}
    except KeyError: raise HTTPException(401, "Executor credentials are invalid or revoked.")

@app.post("/v1/executors/steps/{step_id}/complete")
async def complete_executor_step(step_id: str, body: ExecutorComplete, identity: tuple[str, str] = Depends(executor_identity)):
    try:
        store.complete_executor_step(*identity, step_id, body.result)
        return {"ok": True}
    except KeyError: raise HTTPException(409, "Step is not leased to this executor.")

@app.post("/v1/executors/steps/{step_id}/fail")
async def fail_executor_step(step_id: str, body: ExecutorFailure, identity: tuple[str, str] = Depends(executor_identity)):
    try:
        return {"outcome": store.fail_executor_step(*identity, step_id, body.error)}
    except KeyError: raise HTTPException(409, "Step is not leased to this executor.")

@app.put("/v1/integrations/{provider}")
async def configure_integration(provider: str, body: IntegrationConfigure, user: str = Depends(account_id)):
    if provider not in {"gmail", "calendar", "telegram", "github", "drive"}:
        raise HTTPException(404, "Unknown integration provider.")
    return store.configure_integration(user, provider, **body.model_dump())

@app.put("/v1/integrations/{provider}/credential")
async def store_integration_credential(provider: str, body: IntegrationCredentialInput, user: str = Depends(account_id)):
    if provider not in {"gmail", "calendar", "telegram", "github", "drive"}:
        raise HTTPException(404, "Unknown integration provider.")
    try:
        encrypted = SecretVault(settings.integration_master_keys).encrypt(body.secret)
        store.store_integration_credential(user, provider, body.kind, encrypted)
    except (KeyError, RuntimeError) as exc:
        raise HTTPException(409, str(exc))
    return {"ok": True, "provider": provider, "credential_stored": True}

@app.get("/v1/integrations/{provider}/oauth/start")
async def begin_integration_oauth(provider: str, user: str = Depends(account_id)):
    try:
        url, state, verifier = integration_oauth.begin(provider)
        store.create_oauth_state(user, provider, state, verifier)
        return {"authorization_url": url}
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(503, str(exc))

@app.get("/v1/integrations/{provider}/oauth/callback")
async def finish_integration_oauth(provider: str, code: str, state: str):
    try:
        oauth = store.consume_oauth_state(state, provider)
        token = await integration_oauth.exchange(provider, code, oauth["code_verifier"])
        encrypted = SecretVault(settings.integration_master_keys).encrypt(json.dumps(token))
        try:
            store.integration(oauth["account_id"], provider)
        except KeyError:
            store.configure_integration(oauth["account_id"], provider, display_name=provider.title(), policy="assisted", granted_scopes=[], health="not_connected")
        store.store_integration_credential(oauth["account_id"], provider, "oauth_token", encrypted)
        return {"ok": True, "provider": provider, "connected": True}
    except (KeyError, ValueError, RuntimeError, httpx.HTTPError) as exc:
        raise HTTPException(400, str(exc))

@app.get("/v1/integrations")
async def list_integrations(user: str = Depends(account_id)):
    return {"integrations": store.integrations(user)}

@app.post("/v1/integration-actions", status_code=201)
async def request_integration_action(body: IntegrationActionCreate, background: BackgroundTasks, user: str = Depends(account_id)):
    try:
        action = store.request_integration_action(user, body.provider, body.action, body.preview, body.idempotency_key, body.payload)
        if action["status"] == "awaiting_approval":
            background.add_task(push.send, store, user, "Smara approval needed", action["preview"], "/app/")
        return action
    except KeyError:
        raise HTTPException(409, "Configure this integration before requesting an action.")

@app.get("/v1/integration-actions")
async def list_integration_actions(user: str = Depends(account_id)):
    return {"actions": store.integration_actions(user)}

@app.get("/v1/push/public-key")
async def push_public_key():
    return {"public_key": settings.vapid_public_key or None}

@app.post("/v1/push/subscriptions", status_code=201)
async def subscribe_push(body: PushSubscriptionInput, user: str = Depends(account_id)):
    store.save_push_subscription(user, body.endpoint, body.p256dh, body.auth)
    return {"ok": True}

@app.post("/v1/push/test")
async def test_push(user: str = Depends(account_id)):
    return {"delivered": push.send(store, user, "Smara phone companion", "Push notifications are connected.")}

@app.post("/v1/captures/text", status_code=201)
async def capture_text(title: str = Form(..., max_length=200), text: str = Form(..., max_length=20_000), user: str = Depends(account_id)):
    return store.create_capture(user, "text", title, text)

@app.post("/v1/captures/media", status_code=201)
async def capture_media(title: str = Form(..., max_length=200), file: UploadFile = File(...), user: str = Depends(account_id)):
    allowed = {"image/jpeg", "image/png", "image/webp", "audio/webm", "audio/mpeg", "audio/mp4", "audio/wav"}
    if file.content_type not in allowed:
        raise HTTPException(415, "Only JPG, PNG, WebP, WebM, MP3, M4A, and WAV captures are accepted.")
    limit = 4 * 1024 * 1024 if file.content_type.startswith("image/") else 10 * 1024 * 1024
    content = await file.read(limit + 1)
    if len(content) > limit:
        raise HTTPException(413, "Capture exceeds its size limit.")
    kind = "photo" if file.content_type.startswith("image/") else "voice"
    payload = base64.b64encode(content).decode()
    return store.create_capture(user, kind, title, payload, file.content_type)

@app.post("/v1/integration-actions/{action_id}/approval")
async def decide_integration_action(action_id: str, body: IntegrationActionDecision, user: str = Depends(account_id)):
    try:
        return store.decide_integration_action(user, action_id, body.approved, body.note, body.edited_preview, body.edited_payload)
    except KeyError:
        raise HTTPException(404, "Integration action not found.")
    except ValueError as exc:
        raise HTTPException(409, str(exc))

@app.post("/v1/research", response_model=TaskView, status_code=201)
async def create_research_task(body: ResearchTaskCreate, user: str = Depends(account_id)):
    return view(store.create_research(user, body.workspace_id, body.title, body.question, [str(source) for source in body.sources]))

@app.get("/v1/tasks", response_model=list[TaskView])
async def list_tasks(user: str = Depends(account_id)):
    return [view(row) for row in store.list(user)]

@app.get("/v1/schedules", response_model=list[ScheduleView])
async def list_schedules(user: str = Depends(account_id)):
    return [schedule_view(row) for row in store.schedules(user)]

@app.post("/v1/schedules", response_model=ScheduleView, status_code=201)
async def create_schedule(body: ScheduleCreate, user: str = Depends(account_id)):
    starts_at = body.starts_at or (datetime.now(timezone.utc) + timedelta(seconds=body.interval_seconds))
    if starts_at.tzinfo is None:
        starts_at = starts_at.replace(tzinfo=timezone.utc)
    steps = [step.model_dump() for step in body.steps]
    return schedule_view(store.create_schedule(user, body.workspace_id, body.title, body.objective, body.interval_seconds, starts_at.isoformat(), body.requires_approval, steps))

@app.delete("/v1/schedules/{schedule_id}", status_code=204)
async def cancel_schedule(schedule_id: str, user: str = Depends(account_id)):
    try:
        store.cancel_schedule(schedule_id, user)
    except KeyError:
        raise HTTPException(404, "Schedule not found.")

@app.get("/v1/dead-letters")
async def list_dead_letters(user: str = Depends(account_id)):
    """Failure queue for operator/user review; retries never happen silently."""
    return {"dead_letters": store.dead_letters(user)}

@app.get("/v1/account/export")
async def export_account(user: str = Depends(account_id)):
    return store.audit_export(user)

@app.delete("/v1/account", status_code=204)
async def delete_account(body: AccountDeletionRequest, user: str = Depends(account_id)):
    if not hmac.compare_digest(body.confirm_account_id, user):
        raise HTTPException(400, "confirm_account_id must match the authenticated account.")
    store.delete_account(user)

@app.get("/v1/tasks/{task_id}", response_model=TaskView)
async def get_task(task_id: str, user: str = Depends(account_id)):
    try: return view(store.get(task_id, user))
    except KeyError: raise HTTPException(404, "Task not found")

@app.get("/v1/tasks/{task_id}/events")
async def task_events(task_id: str, user: str = Depends(account_id)):
    try: return {"events": store.events(task_id, user)}
    except KeyError: raise HTTPException(404, "Task not found")


@app.get("/v1/tasks/{task_id}/events/stream")
async def task_events_stream(task_id: str, last_event_id: str | None = Header(default=None, alias="Last-Event-ID"), user: str = Depends(account_id)):
    """Stream durable task events without exposing reasoning or secrets."""
    try:
        store.get(task_id, user)
    except KeyError:
        raise HTTPException(404, "Task not found")

    async def emit():
        sent = 0
        if last_event_id:
            existing = store.events(task_id, user)
            for index, event in enumerate(existing):
                if event["id"] == last_event_id:
                    sent = index + 1
                    break
        started = time.monotonic()
        while time.monotonic() - started < 900:
            events = store.events(task_id, user)
            for event in events[sent:]:
                yield f"id: {event['id']}\nevent: task_update\ndata: {json.dumps(event, default=str)}\n\n"
            sent = len(events)
            task = store.get(task_id, user)
            if task["status"] in {"completed", "failed", "cancelled"}:
                yield f"event: done\ndata: {json.dumps({'status': task['status']})}\n\n"
                return
            yield ": keepalive\n\n"
            await asyncio.sleep(1)
        yield "event: done\ndata: {\"status\":\"timeout\"}\n\n"

    return StreamingResponse(emit(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

@app.get("/v1/tasks/{task_id}/steps")
async def task_steps(task_id: str, user: str = Depends(account_id)):
    try: return {"steps": store.steps(task_id, user)}
    except KeyError: raise HTTPException(404, "Task not found")

@app.get("/v1/research/{task_id}/evidence", response_model=list[EvidenceView])
async def research_evidence(task_id: str, user: str = Depends(account_id)):
    try: return [evidence_view(row) for row in store.evidence(task_id, user)]
    except KeyError: raise HTTPException(404, "Task not found")

@app.get("/v1/tasks/{task_id}/artifacts", response_model=list[ArtifactView])
async def task_artifacts(task_id: str, user: str = Depends(account_id)):
    try: return [artifact_view(row) for row in store.artifacts(task_id, user)]
    except KeyError: raise HTTPException(404, "Task not found")

@app.post("/v1/tasks/{task_id}/approval", response_model=TaskView)
async def approve(task_id: str, body: ApprovalDecision, user: str = Depends(account_id)):
    try: return view(store.decide(task_id, user, body.approved, body.note))
    except KeyError: raise HTTPException(404, "Task not found")

@app.post("/v1/tasks/{task_id}/cancel", response_model=TaskView)
async def cancel_task(task_id: str, user: str = Depends(account_id)):
    try: return view(store.cancel(task_id, user))
    except KeyError: raise HTTPException(404, "Task not found")
