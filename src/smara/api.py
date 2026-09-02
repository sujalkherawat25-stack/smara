from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import re
import time
import base64
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from datetime import datetime, timezone, timedelta
from fastapi import BackgroundTasks, Cookie, Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
import httpx
import jwt

from .config import settings
from .models import AccountDeletionRequest, ApprovalDecision, ArtifactView, ChatRequest, ChatResponse, CliDeviceAuthorize, CliPairingExchange, CliPairingStart, EvidenceView, ExecutorComplete, ExecutorFailure, ExecutorHeartbeat, ExecutorPairingCreate, ExecutorPairRequest, ExecutorProgress, IntegrationActionCreate, IntegrationActionDecision, IntegrationConfigure, IntegrationCredentialInput, PushSubscriptionInput, ResearchTaskCreate, ScheduleCreate, ScheduleView, SkillCreate, SkillTeachRequest, SkillTestRequest, TaskCreate, TaskView, ToolInvokeRequest
from .store import open_task_store
from .agent_runtime import OpenAICompatibleProvider, SmaraAgentRuntime
from .agent_routing import route_request
from . import agent_events, llm_errors
from .syntarus_adapter import SyntarusMemory
from .vault import SecretVault
from .integrations import connected_integration_runner
from . import integration_oauth
from . import push
from .hardening import RedisFixedWindowLimiter
from .observability import configure_sentry
from .tool_registry import ToolContext, ToolError, default_tool_registry
from .provider_routing import resolve_profile
from .provider_routing import load_profiles
from .plugins import manifests
from .attachments import AttachmentStore, MAX_BATCH_BYTES, MAX_ATTACHMENTS_PER_BATCH, MAX_FILE_BYTES
from .auth import account_store, router as auth_router, verify_session_cookie
from .admin import configure as configure_admin, router as admin_router
from .performance import TimingTrace, request_id
from .runtime_resources import RuntimeResources
from .store_async import AsyncStoreFacade
from .work_signals import wait_for_signal, WorkSignalBus
from .workspace_contract import validate_workspace_job, workspace_job_summary
from .skill_protocol import draft_skill_from_workflow, validate_skill_manifest
from .profile_memory import explicit_profile_facts, profile_context

LOG = logging.getLogger("smara.api")
configure_sentry(settings.sentry_dsn)


@asynccontextmanager
async def lifespan(app: FastAPI):
    resources = await RuntimeResources.create(settings) if settings.pooled_resources_enabled else None
    store_facade = AsyncStoreFacade(store)
    app.state.runtime_resources = resources
    app.state.async_store = store_facade
    try:
        yield
    finally:
        await store_facade.close()
        close_store = getattr(store, "close", None)
        if close_store is not None:
            close_store()
        if resources is not None:
            await resources.aclose()


app = FastAPI(title="Smara Control Plane", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.allowed_origins.split(",") if origin.strip()],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Last-Event-ID"],
)
app.include_router(auth_router)
store = open_task_store(database_url=settings.database_url, database_path=settings.database_path, redis_url=settings.redis_url if settings.work_signals_enabled else "")
async_store = AsyncStoreFacade(store)  # fallback for direct/unit callers outside lifespan
configure_admin(store, account_store)
app.include_router(admin_router)
attachment_store = AttachmentStore(Path(settings.database_path or "./data/smara.db").parent / "attachments")
limiter = RedisFixedWindowLimiter(settings.redis_url, settings.rate_limit_per_minute, allow_local_fallback=settings.dev_mode)
MEMORY_WRITE_TIMEOUT_SECONDS = 8.0
def _agent_runtime(model_profile: str | None = None) -> SmaraAgentRuntime:
    """Construct the runtime without importing any MemoryOS implementation."""
    resources = getattr(app.state, "runtime_resources", None)
    if resources is not None:
        return resources.runtime(settings, model_profile)
    memory = None
    if settings.syntarus_api_key:
        from syntarus import AsyncMemoryClient
        memory = SyntarusMemory(AsyncMemoryClient(settings.syntarus_api_key, base_url=settings.syntarus_base_url))
    profile = resolve_profile(
        raw=settings.llm_profiles,
        requested=model_profile or settings.llm_default_profile or None,
        fallback_base_url=settings.llm_base_url,
        fallback_key=settings.llm_api_key,
        fallback_model=settings.llm_model,
        fallback_provider=settings.llm_provider,
    )
    provider = OpenAICompatibleProvider(
        base_url=profile.base_url,
        api_key=profile.api_key,
        model=profile.model,
        auth_header=profile.auth_header,
    )
    # Capability is operator-controlled profile metadata.  It determines
    # whether uploaded image bytes may be sent to the provider; ordinary chat
    # profiles receive only the safe attachment metadata/preview.
    provider.capability = profile.capability
    return SmaraAgentRuntime(provider, memory=memory)


@asynccontextmanager
async def _request_http_client():
    """Use the lifespan pool, with an isolated fallback for direct tests."""
    resources = getattr(app.state, "runtime_resources", None)
    if resources is not None:
        yield resources.http_client
        return
    async with httpx.AsyncClient(timeout=httpx.Timeout(20.0), follow_redirects=False) as client:
        yield client


async def _close_request_memory(runtime: SmaraAgentRuntime) -> None:
    if getattr(runtime, "_shared_resources", False):
        return
    memory = getattr(runtime, "_memory", None)
    if memory is not None:
        await memory.aclose()


async def _remember_chat_turn(
    runtime: SmaraAgentRuntime | None,
    *,
    account_id: str,
    workspace_id: str,
    conversation_id: str,
    user_message: str,
    assistant_message: str,
) -> bool:
    """Best-effort persistence for every completed hosted conversation turn.

    Smara's Postgres conversation log remains the source for bounded prompt
    history.  Syntarus is the durable cross-conversation memory plane.  A
    provider outage must never turn a successful chat into a failed request,
    so this helper is bounded and reports the failure without leaking content.
    """
    memory = getattr(runtime, "_memory", None) if runtime is not None else None
    if memory is None or not user_message.strip() or not assistant_message.strip():
        return False
    try:
        await asyncio.wait_for(
            memory.remember_conversation_turn(
                account_id=account_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                user_message=user_message,
                assistant_message=assistant_message,
            ),
            timeout=MEMORY_WRITE_TIMEOUT_SECONDS,
        )
        return True
    except TimeoutError:
        LOG.warning("conversation_memory_write_timeout account=%s workspace=%s", account_id, workspace_id)
    except Exception as exc:
        LOG.warning(
            "conversation_memory_write_failed account=%s workspace=%s error_type=%s",
            account_id,
            workspace_id,
            type(exc).__name__,
        )
    return False


async def _remember_explicit_profile_facts(*, account_id: str, workspace_id: str, user_message: str) -> str:
    """Persist only direct user-stated profile facts before future recall.

    Syntarus retains the complete conversation-memory event.  This compact
    account-scoped index makes identity recall deterministic while that
    semantic memory is being indexed, and remains usable if retrieval is
    briefly unavailable.
    """
    facts = explicit_profile_facts(user_message)
    if facts:
        await _async_store().call("remember_account_facts", account_id, workspace_id, facts)
    existing = await _async_store().call("account_memory_facts", account_id, workspace_id)
    return profile_context(existing)


async def _durable_profile_context(*, account_id: str, workspace_id: str) -> str:
    loaded = await _async_store().call("account_memory_facts", account_id, workspace_id)
    facts = dict(loaded) if isinstance(loaded, dict) else {}
    # Authentication already has a stable, account-scoped display name. Use
    # it as a fallback for a new user before they have explicitly supplied a
    # preferred name in chat; never overwrite an explicit preference.
    if not facts.get("preferred_name"):
        try:
            account = await asyncio.to_thread(account_store.account_by_id, account_id)
        except Exception:
            account = None
        display_name = str((account or {}).get("display_name") or "").strip()
        if display_name:
            facts["preferred_name"] = display_name[:180]
    return profile_context(facts)


def _queue_durable_chat_task(body: ChatRequest, user: str) -> tuple[dict, str]:
    """Turn a chat request with side effects into safe planning work.

    The initial hosted planning step is read-only and therefore starts
    immediately.  If it needs this PC, the planner creates a second desktop
    task which is always approval-gated before the executor can claim it.
    This gives ordinary chat the expected handoff without weakening the local
    boundary.
    """
    compact = re.sub(r"\s+", " ", body.message).strip()
    title = f"Smara task: {compact[:72]}".rstrip(" .,:;-") or "Smara task"
    task = store.create(
        user,
        body.workspace_id,
        title,
        body.message,
        False,
        [{"name": "agent.execute", "executor_kind": "hosted"}],
    )
    message = (
        f"I created {title} and started the safe planning pass. "
        "If it needs your local files, apps, or document creation, Smara will create a separate task for you to review and approve before this PC does anything."
    )
    return task, message

@app.get("/", include_in_schema=False)
async def web_root():
    # The browser UI is served by the frontend container at the canonical
    # public root. Keep this API root metadata-only so an API misroute cannot
    # accidentally expose a second, stale control surface.
    return {"ok": True, "service": "smara-api"}

@app.middleware("http")
async def harden_http(request: Request, call_next):
    trace = TimingTrace(request_id(request.headers.get("X-Request-ID")))
    request.state.smara_timing = trace
    if request.url.path.startswith("/v1/"):
        client = request.client.host if request.client else "unknown"
        try:
            allowed = await limiter.allow(client)
        except RuntimeError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=503)
        if not allowed:
            return JSONResponse({"detail": "Rate limit exceeded. Try again shortly."}, status_code=429, headers={"Retry-After": "60"})
    response = await call_next(request)
    response.headers["X-Request-ID"] = trace.trace_id
    response.headers.update({"X-Content-Type-Options": "nosniff", "Referrer-Policy": "strict-origin-when-cross-origin", "Permissions-Policy": "camera=(self), microphone=(self), geolocation=()"})
    response.headers["X-Frame-Options"] = "DENY"
    return response

def account_id(
    x_smara_account_id: str | None = Header(default=None),
    x_smara_gateway_timestamp: str | None = Header(default=None),
    x_smara_gateway_signature: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
    smara_session: str | None = Cookie(default=None, alias=settings.auth_cookie_name),
    x_smara_internal_token: str | None = Header(default=None),
) -> str:
    """Resolve an account from a development header or trusted production bridge.

    Production supports either a short-lived JWT minted after an existing Smara
    web-session check, or a signed server-to-server gateway assertion.  Neither
    signing secret is ever exposed to a browser.
    """
    # Native Smara session cookie is the primary browser identity after the
    # cutover.  The DB-backed jti check makes logout/revocation immediate.
    native_subject = verify_session_cookie(smara_session)
    if native_subject:
        return native_subject
    # Telegram/worker traffic is server-to-server and has no browser cookie.
    # Require both values; possession of the internal token alone never grants
    # access to an arbitrary account.
    if settings.internal_token and x_smara_internal_token and hmac.compare_digest(settings.internal_token, x_smara_internal_token):
        if isinstance(x_smara_account_id, str) and x_smara_account_id.startswith("acct_"):
            return x_smara_account_id
        raise HTTPException(401, "A valid Smara account id is required.")
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
                    if claims.get("device_registered") is not True:
                        raise HTTPException(401, "This legacy Smara CLI login must be renewed in the browser.")
                    jti = claims.get("jti")
                    if not isinstance(jti, str) or not store.cli_device_active(subject, jti):
                        raise HTTPException(401, "This Smara CLI device has expired or been revoked.")
                    return subject
            except HTTPException:
                raise
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
    if not x_smara_account_id.startswith("acct_"):
        raise HTTPException(401, "Invalid account assertion.")
    return x_smara_account_id


def _require_hosted_user_integrations() -> None:
    """Fail closed when a request would store/use a private credential.

    The default hosted deployment is a control plane. Personal OAuth/API
    credentials and browser sessions belong on the paired local device; the
    VM runs only operator-owned providers and coordination until a local
    integration adapter is available.
    """
    if not settings.hosted_user_integrations_enabled:
        raise HTTPException(
            409,
            "Personal integrations are local-only on this Smara deployment; no user secret is stored or used on the hosted VM.",
        )

def _as_datetime(value: datetime | str) -> datetime:
    return value if isinstance(value, datetime) else datetime.fromisoformat(value)


def view(row: dict) -> TaskView:
    return TaskView(**{
        **row,
        "requires_approval": bool(row["requires_approval"]),
        "approval_mode": row.get("approval_mode") or "hosted",
        # `result_summary` is the durable final answer. Keep the wire name
        # short and provider-neutral for Web, CLI, and future clients.
        "result": row.get("result_summary") or None,
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


def public_step_view(row: dict) -> dict:
    """Expose step progress without echoing arbitrary local payloads.

    Desktop payloads can contain local paths and credential aliases. The run
    consoles only need the contract metadata and operation label, never the
    original command, content, or secret-shaped fields.
    """
    value = dict(row)
    raw_payload = value.pop("executor_payload", None)
    payload: dict = {}
    if isinstance(raw_payload, str):
        try:
            parsed = json.loads(raw_payload)
            if isinstance(parsed, dict):
                payload = parsed
        except (TypeError, ValueError):
            payload = {}
    elif isinstance(raw_payload, dict):
        payload = raw_payload
    value["attempt"] = int(value.get("attempts") or 0)
    value["error"] = value.get("last_error")
    if isinstance(payload.get("operation"), str):
        value["operation"] = payload["operation"][:80]
    if isinstance(payload.get("stage"), str):
        value["stage"] = payload["stage"][:32]
    if isinstance(payload.get("workspace_job"), dict):
        try:
            value["workspace_job"] = workspace_job_summary(validate_workspace_job(payload["workspace_job"]))
        except RuntimeError:
            value["workspace_job"] = {"schema_version": "invalid"}
    # Internal lease and payload columns are not part of the public step view.
    for key in ("lease_owner", "lease_expires_at", "idempotency_key"):
        value.pop(key, None)
    return value

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


def skill_view(row: dict) -> dict:
    """Return an account-scoped skill record without internal DB fields."""
    return {
        "id": row["id"],
        "name": row["name"],
        "version": row["version"],
        "state": row["state"],
        "manifest": row["manifest"],
        "fingerprint": row["fingerprint"],
        "tested": bool(row.get("tested")),
        "test_run_id": row.get("test_run_id"),
        "approved_by": row.get("approved_by"),
        "created_at": _as_datetime(row["created_at"]),
        "updated_at": _as_datetime(row["updated_at"]),
    }

@app.get("/health")
async def health():
    return {
        "ok": True,
        "memory_boundary": "syntarus-sdk-only",
        "auth_mode": "native-session" if settings.session_secret and settings.accounts_database_url else ("development" if settings.dev_mode else "signed-gateway"),
        "telegram": bool(settings.telegram_bot_token),
        "rollout": {
            "fast_routing": settings.fast_routing_enabled,
            "pooled_resources": settings.pooled_resources_enabled,
            "work_signals": settings.work_signals_enabled,
            "desktop_long_poll": settings.desktop_long_poll_enabled,
            "shadow_routing": settings.shadow_routing_enabled,
            "worker_concurrency": settings.worker_concurrency,
        },
    }

@app.get("/v1/models")
async def list_models(user: str = Depends(account_id)):
    """Return the operator-approved model catalogue without exposing keys."""
    try:
        profiles = load_profiles(
            settings.llm_profiles,
            fallback_base_url=settings.llm_base_url,
            fallback_key=settings.llm_api_key,
            fallback_model=settings.llm_model,
            fallback_provider=settings.llm_provider,
        )
    except ValueError as exc:
        raise HTTPException(503, str(exc)) from exc
    return {
        "models": [
            {
                "name": profile.name,
                "model": profile.model,
                "capability": profile.capability,
                "configured": bool(profile.api_key),
                "default": profile.name == (settings.llm_default_profile or settings.llm_provider),
            }
            for profile in profiles.values()
        ]
    }

@app.post("/v1/attachments")
async def upload_attachments(
    files: list[UploadFile] = File(...),
    user: str = Depends(account_id),
):
    """Store up to 150 MB of user-owned files for the next chat turn.

    Any file type may be attached. Text and common office formats get a
    bounded preview; other binaries remain available as metadata so the agent
    can explain what it can and cannot inspect instead of failing silently.
    """
    if not files:
        raise HTTPException(400, "Choose at least one file to attach.")
    if len(files) > MAX_ATTACHMENTS_PER_BATCH:
        raise HTTPException(413, "Attach at most 10 files at a time.")
    saved: list[dict] = []
    total = 0
    try:
        for upload in files:
            record = await attachment_store.save(user, upload, size_limit=MAX_FILE_BYTES)
            saved.append(record)
            total += int(record["size"])
            if total > MAX_BATCH_BYTES:
                raise ValueError("Attachments exceed the 150 MB total limit per message.")
    except ValueError as exc:
        for record in saved:
            attachment_store.delete(user, record["id"])
        raise HTTPException(413, str(exc)) from exc
    finally:
        for upload in files:
            await upload.close()
    return {
        "attachments": [
            {k: record[k] for k in ("id", "filename", "content_type", "size", "sha256")}
            for record in saved
        ],
        "total_bytes": total,
        "limits": {"per_file_bytes": MAX_FILE_BYTES, "total_bytes": MAX_BATCH_BYTES},
    }

@app.get("/v1/tools")
async def list_tools(user: str = Depends(account_id)):
    """Return the safe tools currently available to the agent runtime."""
    return {"tools": default_tool_registry(include_user_integrations=settings.hosted_user_integrations_enabled).describe()}

@app.get("/v1/plugins")
async def list_plugins(user: str = Depends(account_id)):
    """List built-in and explicitly configured plugin descriptors.

    This is a catalogue only; untrusted plugin code is never imported by the
    API process. External MCP execution needs a separately authenticated
    adapter and approval policy.
    """
    try:
        return {"plugins": manifests(settings.plugin_manifests, include_user_integrations=settings.hosted_user_integrations_enabled)}
    except ValueError as exc:
        raise HTTPException(503, str(exc)) from exc

@app.post("/v1/tools/{tool_name}")
async def invoke_tool(tool_name: str, body: ToolInvokeRequest, user: str = Depends(account_id)):
    """Run one read-only tool; side effects must use the durable task graph."""
    async with httpx.AsyncClient(timeout=httpx.Timeout(12.0), follow_redirects=False) as client:
        try:
            result = await default_tool_registry(client, include_user_integrations=settings.hosted_user_integrations_enabled).invoke(
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
    jti = f"cli_{secrets.token_hex(16)}"
    expires_at = datetime.fromtimestamp(now + expires_in, timezone.utc).isoformat()
    store.register_cli_device(account_id, name, jti, expires_at)
    token = jwt.encode({
        "sub": account_id, "name": name, "jti": jti, "device_registered": True,
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


@app.get("/v1/cli/devices")
async def list_cli_devices(user: str = Depends(account_id)):
    return {"devices": store.cli_devices(user)}


@app.delete("/v1/cli/devices/current", status_code=204)
async def revoke_current_cli_device(authorization: str | None = Header(default=None), user: str = Depends(account_id)):
    if not isinstance(authorization, str) or not authorization.startswith("Bearer ") or not settings.cli_token_secret:
        raise HTTPException(400, "Current request is not authenticated as a CLI device.")
    try:
        claims = jwt.decode(
            authorization.removeprefix("Bearer "), settings.cli_token_secret,
            algorithms=["HS256"], audience="smara-cli", issuer="smara-api",
            options={"require": ["sub", "exp", "iat", "aud", "iss", "jti"]},
        )
        jti = claims["jti"]
        store.revoke_cli_jti(user, jti)
    except (jwt.InvalidTokenError, KeyError) as exc:
        raise HTTPException(404, "Current CLI device was not found or is already revoked.") from exc


@app.delete("/v1/cli/devices/{device_id}", status_code=204)
async def revoke_cli_device(device_id: str, user: str = Depends(account_id)):
    try:
        store.revoke_cli_device(user, device_id)
    except KeyError as exc:
        raise HTTPException(404, "CLI device was not found or is already revoked.") from exc


def _async_store() -> AsyncStoreFacade:
    return getattr(app.state, "async_store", async_store)


async def _conversation(body: ChatRequest, user: str) -> tuple[str, list[dict], str]:
    conversation_id = body.conversation_id or f"chat_{secrets.token_hex(16)}"
    try:
        history, summary = await _async_store().conversation_context(
            conversation_id, user, body.workspace_id
        )
    except KeyError as exc:
        raise HTTPException(404, "Conversation was not found in this account and workspace.") from exc
    return conversation_id, history, summary


def _attachment_context(body: ChatRequest, user: str) -> str:
    if not body.attachment_ids:
        return ""
    context, records = attachment_store.context_for(user, body.attachment_ids)
    found = {record["id"] for record in records}
    missing = [attachment_id for attachment_id in body.attachment_ids if attachment_id not in found]
    if missing:
        raise HTTPException(404, "One or more attachments expired or do not belong to this account.")
    if sum(int(record.get("size", 0)) for record in records) > MAX_BATCH_BYTES:
        raise HTTPException(413, "Attachments exceed the 150 MB total limit per message.")
    return context


def _attachment_images(body: ChatRequest, user: str, runtime: SmaraAgentRuntime) -> list[dict[str, str]]:
    """Inline images only for an explicitly vision-capable model profile."""
    if not body.attachment_ids:
        return []
    provider = getattr(runtime, "_provider", None)
    if getattr(provider, "capability", "chat") != "vision":
        return []
    return attachment_store.image_inputs(user, body.attachment_ids)


@app.get("/v1/conversations")
async def list_conversations(user: str = Depends(account_id)):
    return {"conversations": store.conversations(user)}


@app.get("/v1/conversations/{conversation_id}/turns")
async def conversation_turns(conversation_id: str, workspace_id: str = Query(default="default", min_length=1, max_length=128), user: str = Depends(account_id)):
    try:
        turns, _ = await _async_store().conversation_context(
            conversation_id, user, workspace_id, limit=40, max_chars=30_000
        )
        return {"conversation_id": conversation_id, "turns": turns}
    except KeyError as exc:
        raise HTTPException(404, "Conversation was not found in this account and workspace.") from exc


@app.delete("/v1/conversations/{conversation_id}", status_code=204)
async def delete_conversation(conversation_id: str, user: str = Depends(account_id)):
    try:
        store.delete_conversation(conversation_id, user)
    except KeyError as exc:
        raise HTTPException(404, "Conversation was not found.") from exc


@app.post("/v1/chat", response_model=ChatResponse)
async def chat(body: ChatRequest, user: str = Depends(account_id)):
    """Direct chat with bounded read-only tools; writes remain durable tasks."""
    conversation_id, history, summary = await _conversation(body, user)
    # In the default local-only posture personal connectors (for example the
    # GitHub token stored in Desktop) cannot be invoked by the hosted direct
    # chat registry. Route those requests into the durable approval path so
    # the paired executor can run them with its local vault credential.
    decision = route_request(
        body.message,
        has_attachments=bool(body.attachment_ids),
        local_only=not settings.hosted_user_integrations_enabled,
    )
    if decision.durable_required:
        task, message = _queue_durable_chat_task(body, user)
        await _async_store().call(
            "append_conversation_exchange",
            conversation_id, user, body.workspace_id, body.message, message, None,
        )
        return ChatResponse(
            conversation_id=conversation_id,
            message=message,
            memory_used=False,
            tools_used=0,
        )
    durable_profile = await _durable_profile_context(account_id=user, workspace_id=body.workspace_id)
    attachment_context = _attachment_context(body, user)
    try:
        runtime = _agent_runtime(body.model_profile)
    except ValueError as exc:
        raise HTTPException(503, str(exc)) from exc
    try:
        attachment_images = _attachment_images(body, user, runtime)
        async with _request_http_client() as client:
            turn = await runtime.chat_with_tools(
                account_id=user,
                workspace_id=body.workspace_id,
                message=body.message,
                attachment_images=attachment_images,
                conversation_id=conversation_id,
                conversation_history=history,
                conversation_summary=summary,
                durable_profile_context=durable_profile,
                attachment_context=attachment_context,
                http_client=client,
                integration_runner=(connected_integration_runner(
                    store, user, client, settings.integration_master_keys
                ) if settings.hosted_user_integrations_enabled else None),
                include_user_integrations=settings.hosted_user_integrations_enabled,
            )
        await _async_store().call(
            "append_conversation_exchange",
            conversation_id, user, body.workspace_id, body.message, turn.message, turn.model,
        )
        await _remember_explicit_profile_facts(account_id=user, workspace_id=body.workspace_id, user_message=body.message)
        await _remember_chat_turn(
            runtime,
            account_id=user,
            workspace_id=body.workspace_id,
            conversation_id=conversation_id,
            user_message=body.message,
            assistant_message=turn.message,
        )
        return ChatResponse(**turn.__dict__)
    except httpx.HTTPStatusError as exc:
        # Keep non-streaming clients (CLI, integrations, API consumers) on
        # the same safe, actionable error contract as the SSE client.  In
        # particular Sarvam's beta-gated GLM/Gemma responses become a clear
        # model-unavailable message rather than an opaque 502.
        kind, message = llm_errors.describe(
            exc, provider=body.model_profile or settings.llm_provider
        )
        raise HTTPException(503, message, headers={"X-Smara-Error": kind}) from exc
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    finally:
        await _close_request_memory(runtime)


@app.post("/v1/chat/stream")
async def chat_stream(request: Request, body: ChatRequest, user: str = Depends(account_id)):
    """SSE view of direct chat and bounded read-only tool progress."""
    conversation_id, history, summary = await _conversation(body, user)
    attachment_context = _attachment_context(body, user)
    async def emit():
        started_at = time.perf_counter()
        trace = getattr(request.state, "smara_timing", TimingTrace())
        trace.mark("route_started")
        decision = route_request(
            body.message,
            has_attachments=bool(body.attachment_ids),
            local_only=not settings.hosted_user_integrations_enabled,
        )
        if decision.durable_required:
            task, message = _queue_durable_chat_task(body, user)
            yield agent_events.phase("triage")
            yield agent_events.status("Creating an approval-gated Smara task", detail="Planning can start now; local execution will wait for your approval.")
            yield agent_events.phase("answer")
            yield agent_events.token(message)
            await _async_store().call(
                "append_conversation_exchange",
                conversation_id, user, body.workspace_id, body.message, message, None,
            )
            trace.mark("persisted")
            yield agent_events.done(
                memory_used=False,
                tools_used=0,
                total_ms=agent_events.elapsed_ms(started_at),
                request_id=trace.trace_id,
                timings=trace.as_dict(),
                task_id=task["id"],
            )
            return
        durable_profile = await _durable_profile_context(account_id=user, workspace_id=body.workspace_id)
        try:
            runtime = _agent_runtime(body.model_profile)
        except ValueError as exc:
            kind, message = llm_errors.describe(exc, provider=settings.llm_provider)
            yield agent_events.error(message, kind=kind)
            return
        queue: asyncio.Queue[str] = asyncio.Queue()
        # A runtime may announce the answer phase before its first token, while
        # the token hook also needs to announce it for providers that omit the
        # phase. Keep one phase row per turn so the Desktop activity rail stays
        # truthful and readable.
        emitted_phases: set[str] = set()

        def event_hook(event_type: str, payload: dict) -> None:
            if event_type == "agent.phase":
                phase_name = payload.get("phase")
                if phase_name in {"triage", "retrieve", "reason_act", "answer"} and phase_name not in emitted_phases:
                    emitted_phases.add(str(phase_name))
                    queue.put_nowait(agent_events.phase(str(phase_name)))
            elif event_type == "agent.status":
                queue.put_nowait(
                    agent_events.status(
                        str(payload.get("label") or "Working on it"),
                        detail=str(payload.get("detail")) if payload.get("detail") else None,
                    )
                )
            elif event_type == "agent.tool_requested":
                queue.put_nowait(agent_events.tool_call(str(payload.get("tool", "unknown"))))
            elif event_type == "agent.tool_completed":
                meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
                preview = str(payload.get("preview", ""))
                # Keep the activity row useful without exposing raw search
                # snippets.  The full evidence stays in the writer prompt;
                # users only need to know how much was actually checked.
                if payload.get("tool") == "research.deep" and meta:
                    preview = (
                        f"Checked {int(meta.get('queries', 0) if isinstance(meta.get('queries'), int) else len(meta.get('queries', [])))} search angles; "
                        f"read {int(meta.get('fetched', 0) or 0)} of {int(meta.get('sources', 0) or 0)} selected sources."
                    )
                queue.put_nowait(agent_events.tool_result(
                    str(payload.get("tool", "unknown")),
                    ok=bool(payload.get("ok")),
                    preview=preview,
                    citations=payload.get("citations") if isinstance(payload.get("citations"), list) else None,
                ))

        def token_hook(text: str) -> None:
            if "answer" not in emitted_phases:
                queue.put_nowait(agent_events.phase("answer"))
                emitted_phases.add("answer")
            queue.put_nowait(agent_events.token(text))

        async def run_chat():
            attachment_images = _attachment_images(body, user, runtime)
            async with _request_http_client() as client:
                return await runtime.chat_with_tools(
                    account_id=user,
                    workspace_id=body.workspace_id,
                    message=body.message,
                    attachment_images=attachment_images,
                    conversation_id=conversation_id,
                    conversation_history=history,
                    conversation_summary=summary,
                    durable_profile_context=durable_profile,
                    attachment_context=attachment_context,
                    http_client=client,
                    integration_runner=(connected_integration_runner(
                        store, user, client, settings.integration_master_keys
                    ) if settings.hosted_user_integrations_enabled else None),
                    include_user_integrations=settings.hosted_user_integrations_enabled,
                    event_hook=event_hook,
                    token_hook=token_hook,
                )

        task = asyncio.create_task(run_chat())
        try:
            yield agent_events.status("Preparing a bounded Smara response")
            trace.mark("first_status")
            while not task.done():
                try:
                    yield await asyncio.wait_for(queue.get(), timeout=0.1)
                except asyncio.TimeoutError:
                    continue
            while not queue.empty():
                yield queue.get_nowait()
            turn = await task
            await _async_store().call(
                "append_conversation_exchange",
                conversation_id, user, body.workspace_id, body.message, turn.message, turn.model,
            )
            await _remember_explicit_profile_facts(account_id=user, workspace_id=body.workspace_id, user_message=body.message)
            await _remember_chat_turn(
                runtime,
                account_id=user,
                workspace_id=body.workspace_id,
                conversation_id=conversation_id,
                user_message=body.message,
                assistant_message=turn.message,
            )
            trace.mark("persisted")
            yield agent_events.done(
                memory_used=turn.memory_used,
                tools_used=turn.tools_used,
                total_ms=agent_events.elapsed_ms(started_at),
                request_id=trace.trace_id,
                timings=trace.as_dict(),
            )
        except Exception as exc:
            if not task.done():
                task.cancel()
            kind, message = llm_errors.describe(exc, provider=settings.llm_provider)
            # Keep operational breadcrumbs useful without logging raw
            # provider responses, prompts, tokens, or user content.
            LOG.warning(
                "chat_stream_failed kind=%s provider=%s error_type=%s",
                kind,
                settings.llm_provider,
                type(exc).__name__,
            )
            yield agent_events.error(message, kind=kind)
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await _close_request_memory(runtime)

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
    except ValueError as exc: raise HTTPException(400, str(exc)) from exc

@app.get("/v1/executors")
async def list_executors(user: str = Depends(account_id)):
    return {"executors": store.executors(user)}


@app.delete("/v1/executors/{executor_id}", status_code=204)
async def revoke_executor(executor_id: str, user: str = Depends(account_id)):
    try:
        store.revoke_executor(executor_id, user)
    except KeyError as exc:
        raise HTTPException(404, "Desktop executor was not found or is already revoked.") from exc

@app.delete("/v1/executors/{executor_id}/self-revoke", status_code=204)
async def self_revoke_executor(executor_id: str, identity: tuple[str, str] = Depends(executor_identity)):
    paired_executor_id, token = identity
    if paired_executor_id != executor_id:
        raise HTTPException(403, "An executor may revoke only itself.")
    try:
        store.revoke_executor_with_token(executor_id, token)
    except KeyError as exc:
        raise HTTPException(404, "Desktop executor was not found or is already revoked.") from exc

@app.post("/v1/executors/claim")
async def claim_executor(
    wait_seconds: float = Query(default=5.0, ge=0.0, le=25.0),
    auto_approve_safe: bool = Query(default=False),
    auto_approve_local: bool = Query(default=False),
    identity: tuple[str, str] = Depends(executor_identity),
):
    """Claim immediately, then wait on an advisory signal before repairing.

    The database claim is always authoritative. Redis only wakes an idle
    desktop sooner; a missing signal simply falls back to the bounded wait and
    second claim, so work cannot be lost.
    """
    try:
        step = await _async_store().call("claim_for_executor", *identity, auto_approve_safe=auto_approve_safe, auto_approve_local=auto_approve_local)
        effective_wait = wait_seconds if settings.desktop_long_poll_enabled else 0.0
        if step is None and effective_wait:
            await wait_for_signal(settings.redis_url if settings.work_signals_enabled else "", effective_wait)
            step = await _async_store().call("claim_for_executor", *identity, auto_approve_safe=auto_approve_safe, auto_approve_local=auto_approve_local)
        return {"step": step}
    except KeyError:
        raise HTTPException(401, "Executor credentials are invalid or revoked.")

@app.post("/v1/executors/tasks/{task_id}/approval", response_model=TaskView)
async def decide_executor_task(task_id: str, body: ApprovalDecision, identity: tuple[str, str] = Depends(executor_identity)):
    """The sole approval endpoint for paired-Desktop work."""
    try:
        return view(store.decide_for_executor(*identity, task_id, body.approved, body.note))
    except KeyError:
        raise HTTPException(401, "Executor credentials are invalid, revoked, or cannot access this task.")
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc

@app.post("/v1/executors/steps/{step_id}/heartbeat")
async def heartbeat_executor_step(step_id: str, identity: tuple[str, str] = Depends(executor_identity)):
    """Refresh only the lease owned by this paired desktop executor."""
    try:
        return store.heartbeat_executor_step(*identity, step_id)
    except KeyError:
        raise HTTPException(409, "Step is not leased to this executor.")

@app.get("/v1/executors/steps/{step_id}")
async def executor_step_status(step_id: str, identity: tuple[str, str] = Depends(executor_identity)):
    """Reconcile a local journal entry without exposing another account's task."""
    try:
        return store.executor_step_status(*identity, step_id)
    except KeyError:
        raise HTTPException(404, "Step was not found for this executor.")

@app.post("/v1/executors/steps/{step_id}/progress")
async def progress_executor_step(step_id: str, body: ExecutorProgress, identity: tuple[str, str] = Depends(executor_identity)):
    """Record a bounded local status line without uploading command output."""
    try:
        store.append_executor_progress(*identity, step_id, body.message)
        return {"ok": True}
    except KeyError:
        raise HTTPException(409, "Step is not leased to this executor.")

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
    _require_hosted_user_integrations()
    if provider not in {"gmail", "calendar", "telegram", "github", "drive"}:
        raise HTTPException(404, "Unknown integration provider.")
    return store.configure_integration(user, provider, **body.model_dump())

@app.put("/v1/integrations/{provider}/credential")
async def store_integration_credential(provider: str, body: IntegrationCredentialInput, user: str = Depends(account_id)):
    _require_hosted_user_integrations()
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
    _require_hosted_user_integrations()
    try:
        url, state, verifier = integration_oauth.begin(provider)
        store.create_oauth_state(user, provider, state, verifier)
        return {"authorization_url": url}
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(503, str(exc))

@app.get("/v1/integrations/{provider}/oauth/callback")
async def finish_integration_oauth(provider: str, code: str, state: str):
    _require_hosted_user_integrations()
    try:
        oauth = store.consume_oauth_state(state, provider)
        token = await integration_oauth.exchange(provider, code, oauth["code_verifier"])
        encrypted = SecretVault(settings.integration_master_keys).encrypt(json.dumps(token))
        try:
            store.integration(oauth["account_id"], provider)
        except KeyError:
            store.configure_integration(oauth["account_id"], provider, display_name=provider.title(), policy="assisted", granted_scopes=[], health="not_connected")
        store.store_integration_credential(oauth["account_id"], provider, "oauth_token", encrypted)
        label = {"gmail": "Gmail", "calendar": "Google Calendar", "drive": "Google Drive", "github": "GitHub"}.get(provider, "Provider")
        return HTMLResponse(
            "<!doctype html><meta name='viewport' content='width=device-width'>"
            "<title>Smara integration connected</title>"
            "<main style='max-width:36rem;margin:12vh auto;padding:2rem;font:16px system-ui;background:#151a24;color:#eef2ff;border-radius:14px'>"
            f"<h1>{label} connected</h1><p>The encrypted connection is ready. Return to Smara and close this window.</p></main>"
        )
    except (KeyError, ValueError, RuntimeError, httpx.HTTPError) as exc:
        raise HTTPException(400, str(exc))

@app.get("/v1/integrations")
async def list_integrations(user: str = Depends(account_id)):
    if not settings.hosted_user_integrations_enabled:
        return {"integrations": [], "mode": "local-only"}
    return {"integrations": store.integrations(user), "mode": "hosted"}

@app.post("/v1/integration-actions", status_code=201)
async def request_integration_action(body: IntegrationActionCreate, background: BackgroundTasks, user: str = Depends(account_id)):
    _require_hosted_user_integrations()
    try:
        action = store.request_integration_action(user, body.provider, body.action, body.preview, body.idempotency_key, body.payload)
        if action["status"] == "awaiting_approval":
            background.add_task(push.send, store, user, "Smara approval needed", action["preview"], "/")
        return action
    except KeyError:
        raise HTTPException(409, "Configure this integration before requesting an action.")

@app.get("/v1/integration-actions")
async def list_integration_actions(user: str = Depends(account_id)):
    if not settings.hosted_user_integrations_enabled:
        return {"actions": [], "mode": "local-only"}
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
async def capture_media(title: str = Form(..., max_length=200), file: UploadFile = File(...), analysis: str = Form("auto", max_length=16), user: str = Depends(account_id)):
    allowed = {"image/jpeg", "image/png", "image/webp", "application/pdf", "audio/webm", "audio/mpeg", "audio/mp4", "audio/wav"}
    if file.content_type not in allowed:
        raise HTTPException(415, "Only JPG, PNG, WebP, PDF, WebM, MP3, M4A, and WAV captures are accepted.")
    analysis = analysis.strip().lower()
    if analysis not in {"auto", "image", "ocr"}:
        raise HTTPException(422, "analysis must be auto, image, or ocr.")
    if analysis == "ocr" and not (file.content_type.startswith("image/") or file.content_type == "application/pdf"):
        raise HTTPException(422, "OCR analysis is available for images and PDF documents.")
    is_document = file.content_type == "application/pdf" or analysis == "ocr"
    limit = 20 * 1024 * 1024 if is_document else 4 * 1024 * 1024 if file.content_type.startswith("image/") else 10 * 1024 * 1024
    content = await file.read(limit + 1)
    if len(content) > limit:
        raise HTTPException(413, "Capture exceeds its size limit.")
    kind = "document" if is_document else "photo" if file.content_type.startswith("image/") else "voice"
    payload = base64.b64encode(content).decode()
    return store.create_capture(user, kind, title, payload, file.content_type)

@app.post("/v1/integration-actions/{action_id}/approval")
async def decide_integration_action(action_id: str, body: IntegrationActionDecision, user: str = Depends(account_id)):
    _require_hosted_user_integrations()
    try:
        return store.decide_integration_action(user, action_id, body.approved, body.note, body.edited_preview, body.edited_payload)
    except KeyError:
        raise HTTPException(404, "Integration action not found.")
    except ValueError as exc:
        raise HTTPException(409, str(exc))

@app.post("/v1/research", response_model=TaskView, status_code=201)
async def create_research_task(body: ResearchTaskCreate, user: str = Depends(account_id)):
    return view(store.create_research(user, body.workspace_id, body.title, body.question, [str(source) for source in body.sources]))


@app.get("/v1/research", response_model=list[TaskView])
async def list_research_tasks(user: str = Depends(account_id)):
    return [view(row) for row in store.research_tasks(user)]

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


@app.get("/v1/skills")
async def list_skills(user: str = Depends(account_id)):
    return {"skills": [skill_view(row) for row in store.list_skills(user)]}


@app.post("/v1/skills", status_code=201)
async def create_skill(body: SkillCreate, user: str = Depends(account_id)):
    try:
        manifest = validate_skill_manifest(body.manifest)
        return skill_view(store.create_skill(user, manifest.model_dump(mode="json")))
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/v1/skills/teach", status_code=201)
async def teach_skill(body: SkillTeachRequest, user: str = Depends(account_id)):
    try:
        manifest = draft_skill_from_workflow(
            name=body.name,
            version=body.version,
            description=body.description,
            owner=user,
            workflow=body.workflow,
            tests=body.tests,
            rollback=body.rollback,
        )
        return skill_view(store.create_skill(user, manifest.model_dump(mode="json")))
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get("/v1/skills/{name}/{version}")
async def get_skill(name: str, version: str, user: str = Depends(account_id)):
    try:
        return skill_view(store.get_skill(user, name, version))
    except KeyError as exc:
        raise HTTPException(404, "Skill not found.") from exc


@app.post("/v1/skills/{name}/{version}/test")
async def test_skill(name: str, version: str, body: SkillTestRequest, user: str = Depends(account_id)):
    try:
        return skill_view(store.record_skill_test(user, name, version, passed=body.passed, run_id=body.run_id))
    except KeyError as exc:
        raise HTTPException(404, "Skill not found.") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/v1/skills/{name}/{version}/publish")
async def publish_skill(name: str, version: str, user: str = Depends(account_id)):
    try:
        return skill_view(store.publish_skill(user, name, version))
    except KeyError as exc:
        raise HTTPException(404, "Skill not found.") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/v1/skills/{name}/{version}/deprecate")
async def deprecate_skill(name: str, version: str, user: str = Depends(account_id)):
    try:
        return skill_view(store.deprecate_skill(user, name, version))
    except KeyError as exc:
        raise HTTPException(404, "Skill not found.") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc

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
        await _async_store().call("get", task_id, user)
    except KeyError:
        raise HTTPException(404, "Task not found")

    async def emit():
        cursor = last_event_id
        started = time.monotonic()
        while time.monotonic() - started < 900:
            events = await _async_store().call("events_after", task_id, user, cursor)
            for event in events:
                yield f"id: {event['id']}\nevent: task_update\ndata: {json.dumps(event, default=str)}\n\n"
                cursor = event["id"]
            task = await _async_store().call("get", task_id, user)
            if task["status"] in {"completed", "failed", "cancelled"}:
                yield f"event: done\ndata: {json.dumps({'status': task['status']})}\n\n"
                return
            yield ": keepalive\n\n"
            await wait_for_signal(settings.redis_url, 5, enabled=settings.work_signals_enabled)
        yield "event: done\ndata: {\"status\":\"timeout\"}\n\n"

    return StreamingResponse(emit(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

@app.get("/v1/tasks/{task_id}/steps")
async def task_steps(task_id: str, user: str = Depends(account_id)):
    try: return {"steps": [public_step_view(step) for step in store.steps(task_id, user)]}
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
    try:
        task = store.get(task_id, user)
        if task.get("approval_mode", "hosted") == "desktop":
            raise HTTPException(409, "This task must be approved or rejected on its paired Desktop.")
        return view(store.decide(task_id, user, body.approved, body.note))
    except KeyError: raise HTTPException(404, "Task not found")
    except ValueError as exc: raise HTTPException(409, str(exc)) from exc

@app.post("/v1/tasks/{task_id}/cancel", response_model=TaskView)
async def cancel_task(task_id: str, user: str = Depends(account_id)):
    try: return view(store.cancel(task_id, user))
    except KeyError: raise HTTPException(404, "Task not found")

@app.post("/v1/tasks/{task_id}/retry", response_model=TaskView)
async def retry_task(task_id: str, user: str = Depends(account_id)):
    """Retry a failed task with a fresh local approval when needed."""
    try: return view(store.retry_task(task_id, user))
    except KeyError: raise HTTPException(404, "Task not found")
    except ValueError as exc: raise HTTPException(409, str(exc)) from exc
