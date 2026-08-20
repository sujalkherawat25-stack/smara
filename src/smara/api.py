from __future__ import annotations

import hashlib
import hmac
import json
import time
import base64
from datetime import datetime
from pathlib import Path
from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import httpx

from .config import settings
from .models import ApprovalDecision, ArtifactView, EvidenceView, ExecutorComplete, ExecutorFailure, ExecutorHeartbeat, ExecutorPairingCreate, ExecutorPairRequest, IntegrationActionCreate, IntegrationActionDecision, IntegrationConfigure, IntegrationCredentialInput, PushSubscriptionInput, ResearchTaskCreate, TaskCreate, TaskView
from .store import open_task_store
from .vault import SecretVault
from . import integration_oauth
from . import push
from .hardening import FixedWindowLimiter
from .observability import configure_sentry

configure_sentry(settings.sentry_dsn)
app = FastAPI(title="Smara Control Plane", version="0.1.0")
store = open_task_store(database_url=settings.database_url, database_path=settings.database_path)
limiter = FixedWindowLimiter(settings.rate_limit_per_minute)
source_web_dir = Path(__file__).resolve().parents[2] / "web"
web_dir = source_web_dir if source_web_dir.is_dir() else Path("/app/web")
if web_dir.is_dir():
    app.mount("/app", StaticFiles(directory=web_dir, html=True), name="smara-web")

@app.get("/", include_in_schema=False)
async def web_root():
    if web_dir.is_dir():
        return FileResponse(web_dir / "index.html")
    return {"ok": True}

@app.middleware("http")
async def harden_http(request: Request, call_next):
    if request.url.path.startswith("/v1/"):
        client = request.client.host if request.client else "unknown"
        if not limiter.allow(client):
            return JSONResponse({"detail": "Rate limit exceeded. Try again shortly."}, status_code=429, headers={"Retry-After": "60"})
    response = await call_next(request)
    response.headers.update({"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "strict-origin-when-cross-origin", "Permissions-Policy": "camera=(self), microphone=(self), geolocation=()"})
    return response

def account_id(
    x_smara_account_id: str | None = Header(default=None),
    x_smara_gateway_timestamp: str | None = Header(default=None),
    x_smara_gateway_signature: str | None = Header(default=None),
) -> str:
    """Accept a raw identity only in dev; production accepts a gateway assertion.

    The gateway is responsible for session/JWT verification. This API verifies
    that the identity assertion came from that gateway and has not been replayed.
    """
    if settings.dev_mode:
        if not x_smara_account_id:
            raise HTTPException(401, "X-Smara-Account-Id is required in development.")
        return x_smara_account_id
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
    return EvidenceView(**{**row, "retrieved_at": _as_datetime(row["retrieved_at"]) if row.get("retrieved_at") else None})


def artifact_view(row: dict) -> ArtifactView:
    return ArtifactView(**{**row, "created_at": _as_datetime(row["created_at"])})

@app.get("/health")
async def health(): return {"ok": True, "memory_boundary": "syntarus-sdk-only", "auth_mode": "development" if settings.dev_mode else "signed-gateway"}

@app.get("/readyz")
async def readyz():
    try:
        with store._connect() as connection:
            connection.execute("SELECT 1")
        return {"ok": True}
    except Exception:
        raise HTTPException(503, "Smara database is not ready.")

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

@app.get("/v1/dead-letters")
async def list_dead_letters(user: str = Depends(account_id)):
    """Failure queue for operator/user review; retries never happen silently."""
    return {"dead_letters": store.dead_letters(user)}

@app.get("/v1/tasks/{task_id}", response_model=TaskView)
async def get_task(task_id: str, user: str = Depends(account_id)):
    try: return view(store.get(task_id, user))
    except KeyError: raise HTTPException(404, "Task not found")

@app.get("/v1/tasks/{task_id}/events")
async def task_events(task_id: str, user: str = Depends(account_id)):
    try: return {"events": store.events(task_id, user)}
    except KeyError: raise HTTPException(404, "Task not found")

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
