from __future__ import annotations

import hashlib
import hmac
import time
from datetime import datetime
from fastapi import Depends, FastAPI, Header, HTTPException

from .config import settings
from .models import ApprovalDecision, TaskCreate, TaskView
from .store import TaskStore

app = FastAPI(title="Smara Control Plane", version="0.1.0")
store = TaskStore(settings.database_path)

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

def view(row: dict) -> TaskView:
    return TaskView(**{**row, "requires_approval": bool(row["requires_approval"]), "created_at": datetime.fromisoformat(row["created_at"]), "updated_at": datetime.fromisoformat(row["updated_at"])})

@app.get("/health")
async def health(): return {"ok": True, "memory_boundary": "syntarus-sdk-only", "auth_mode": "development" if settings.dev_mode else "signed-gateway"}

@app.post("/v1/tasks", response_model=TaskView, status_code=201)
async def create_task(body: TaskCreate, user: str = Depends(account_id)):
    steps = [{"name": step.name, "depends_on": step.depends_on} for step in body.steps]
    return view(store.create(user, body.workspace_id, body.title, body.objective, body.requires_approval, steps))

@app.get("/v1/tasks", response_model=list[TaskView])
async def list_tasks(user: str = Depends(account_id)):
    return [view(row) for row in store.list(user)]

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

@app.post("/v1/tasks/{task_id}/approval", response_model=TaskView)
async def approve(task_id: str, body: ApprovalDecision, user: str = Depends(account_id)):
    try: return view(store.decide(task_id, user, body.approved, body.note))
    except KeyError: raise HTTPException(404, "Task not found")
