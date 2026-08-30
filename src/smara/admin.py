"""Smara-owned operator console API.

This module is intentionally a small read-only boundary around the Smara
control-plane store. It does not import MemoryOS tables or expose Syntarus
memory contents. Syntarus is represented only by a bounded health probe and
the fact that the SDK boundary is configured.
"""
from __future__ import annotations

import hmac
import logging
import time
from datetime import datetime
from typing import Any

import httpx
import jwt
from fastapi import APIRouter, Cookie, Depends, HTTPException, Response

from .config import settings

LOG = logging.getLogger("smara.admin")
router = APIRouter(prefix="/v1/admin", tags=["operator"])

_task_store: Any = None
_account_store: Any = None


def configure(task_store: Any, account_store: Any) -> None:
    """Bind the already-created stores without creating a second database."""
    global _task_store, _account_store
    _task_store = task_store
    _account_store = account_store


def _operator_cookie(token: str | None) -> str:
    if not settings.operator_secret:
        raise HTTPException(503, "The operator console is not configured on this deployment.")
    if not token:
        raise HTTPException(401, "Operator sign-in is required.")
    try:
        claims = jwt.decode(
            token,
            settings.operator_secret,
            algorithms=["HS256"],
            audience="smara-admin",
            issuer="smara-api",
            options={"require": ["sub", "iat", "exp", "aud", "iss"]},
        )
    except jwt.InvalidTokenError as exc:
        raise HTTPException(401, "Operator session expired. Sign in again.") from exc
    if claims.get("sub") != "operator":
        raise HTTPException(401, "Operator session is invalid.")
    return "operator"


def require_operator(smara_operator: str | None = Cookie(default=None, alias=settings.operator_cookie_name)) -> str:
    return _operator_cookie(smara_operator)


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _short_account(value: Any) -> str:
    text = str(value or "")
    if len(text) <= 18:
        return text or "—"
    return f"{text[:9]}…{text[-6:]}"


def _rows(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    if _task_store is None:
        return []
    try:
        with _task_store._connect() as connection:
            return [dict(row) for row in connection.execute(sql, params).fetchall()]
    except Exception as exc:
        LOG.warning("operator query unavailable: %s", type(exc).__name__)
        return []


def _scalar(sql: str, params: tuple[Any, ...] = ()) -> int:
    values = _rows(sql, params)
    if not values:
        return 0
    value = next(iter(values[0].values()), 0)
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _account_rows() -> list[dict[str, Any]]:
    if _account_store is None:
        return []
    try:
        with _account_store._connect() as connection:
            result = _account_store._execute(
                connection,
                "SELECT id,email,display_name,plan,created_at,last_login_at FROM accounts ORDER BY COALESCE(last_login_at,created_at) DESC",
            ).fetchall()
        return [dict(row) for row in result]
    except Exception as exc:
        LOG.warning("operator account query unavailable: %s", type(exc).__name__)
        return []


def _control_snapshot() -> dict[str, Any]:
    status_rows = _rows("SELECT status,COUNT(*) AS count FROM tasks GROUP BY status")
    task_status = {str(row.get("status")): int(row.get("count") or 0) for row in status_rows}
    total_tasks = sum(task_status.values())
    active_statuses = {"queued", "running", "waiting_approval", "cancelling"}

    executor_rows = _rows(
        "SELECT id,name,account_id,status,capabilities,last_seen_at,created_at FROM desktop_executors ORDER BY last_seen_at DESC"
    )
    for row in executor_rows:
        row["id"] = _short_account(row.get("id"))
        row["account_id"] = _short_account(row.get("account_id"))
        row.pop("token_hash", None)

    people = _account_rows()
    usage_rows = _rows(
        "SELECT account_id,COUNT(*) AS tasks_total,SUM(CASE WHEN status IN ('queued','running','waiting_approval','cancelling') THEN 1 ELSE 0 END) AS tasks_active FROM tasks GROUP BY account_id"
    )
    usage = {str(row.get("account_id")): row for row in usage_rows}
    for person in people:
        info = usage.get(str(person.get("id")), {})
        person["account_id"] = person.pop("id", None)
        person["tasks_total"] = int(info.get("tasks_total") or 0)
        person["tasks_active"] = int(info.get("tasks_active") or 0)
        person.pop("google_sub", None)
        person.pop("avatar_url", None)

    recent_tasks = _rows(
        "SELECT id,title,status,account_id,requires_approval,created_at,updated_at,(result_summary IS NOT NULL AND TRIM(result_summary)!='') AS result_available FROM tasks ORDER BY updated_at DESC LIMIT 12"
    )
    for row in recent_tasks:
        row["task_id"] = row.pop("id", None)
        row["account_id"] = _short_account(row.get("account_id"))
        row["requires_approval"] = bool(row.get("requires_approval"))
        row["result_available"] = bool(row.get("result_available"))

    recent_events = _rows(
        "SELECT task_id,type,created_at FROM task_events ORDER BY created_at DESC LIMIT 16"
    )
    for row in recent_events:
        row["task_id"] = _short_account(row.get("task_id"))

    integrations = _rows(
        "SELECT provider,health,COUNT(*) AS count FROM integration_connections GROUP BY provider,health ORDER BY provider,health"
    )
    integration_count = _scalar("SELECT COUNT(*) AS count FROM integration_connections")
    unresolved_dead_letters = _scalar("SELECT COUNT(*) AS count FROM task_dead_letters WHERE resolved_at IS NULL")
    return _jsonable(
        {
            "boundary": "Smara control plane only",
            "tasks": {
                "total": total_tasks,
                "by_status": task_status,
                "active": sum(task_status.get(status, 0) for status in active_statuses),
                "with_results": _scalar("SELECT COUNT(*) AS count FROM tasks WHERE result_summary IS NOT NULL AND TRIM(result_summary)!=''"),
            },
            "accounts": {
                "total": len(people),
                "people": people[:200],
            },
            "executors": {
                "total": len(executor_rows),
                "online": sum(1 for row in executor_rows if row.get("status") == "active"),
                "items": executor_rows[:100],
            },
            "integrations": {"total": integration_count, "by_provider": integrations},
            "artifacts": {"total": _scalar("SELECT COUNT(*) AS count FROM artifacts")},
            "events": {"total": _scalar("SELECT COUNT(*) AS count FROM task_events"), "recent": recent_events},
            "dead_letters": {"unresolved": unresolved_dead_letters},
            "conversations": {"total": _scalar("SELECT COUNT(*) AS count FROM conversations")},
            "recent_tasks": recent_tasks,
        }
    )


def _syntarus_health_url() -> str:
    if settings.syntarus_health_url:
        return settings.syntarus_health_url
    base = settings.syntarus_base_url.rstrip("/")
    if base.endswith("/v1"):
        origin = base[:-3]
        # The public edge intentionally keeps Syntarus under its own prefix.
        if "ai.syntarus.com" in origin:
            return f"{origin}/syntarus-api/health"
        return f"{origin}/health"
    return f"{base}/health"


async def _syntarus_snapshot() -> dict[str, Any]:
    url = _syntarus_health_url()
    started = time.perf_counter()
    status = "unconfigured"
    detail = "Syntarus SDK key is not configured on Smara."
    ok = False
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(4.0), follow_redirects=False) as client:
            response = await client.get(url, headers={"Accept": "application/json"})
        ok = response.is_success
        status = "healthy" if ok else "degraded"
        if response.headers.get("content-type", "").startswith("application/json"):
            body = response.json()
            detail = str(body.get("status") or body.get("detail") or status)[:240] if isinstance(body, dict) else status
        else:
            detail = response.text[:240] or status
    except Exception as exc:
        status = "unreachable"
        detail = f"Health probe failed ({type(exc).__name__})."
    return {
        "boundary": "Syntarus context plane via SDK/API only",
        "ok": ok and bool(settings.syntarus_api_key),
        "status": status if settings.syntarus_api_key else "unconfigured",
        "detail": detail if settings.syntarus_api_key else "Syntarus SDK key is not configured on Smara.",
        "sdk_configured": bool(settings.syntarus_api_key),
        "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        "health_path": url.split("://", 1)[-1].split("/", 1)[-1] if "://" in url else url,
        "raw_memory_exposed": False,
    }


@router.post("/session")
async def operator_sign_in(body: dict[str, Any], response: Response) -> dict[str, Any]:
    if not settings.operator_secret:
        raise HTTPException(503, "The operator console is not configured on this deployment.")
    supplied = str(body.get("secret") or "")
    if not supplied or not hmac.compare_digest(supplied, settings.operator_secret):
        raise HTTPException(401, "That operator secret was not accepted.")
    now = int(time.time())
    ttl = max(900, min(7 * 24 * 3600, int(settings.operator_session_hours) * 3600))
    token = jwt.encode(
        {"sub": "operator", "iat": now, "exp": now + ttl, "aud": "smara-admin", "iss": "smara-api"},
        settings.operator_secret,
        algorithm="HS256",
    )
    response.set_cookie(
        key=settings.operator_cookie_name,
        value=token,
        max_age=ttl,
        httponly=True,
        secure=not settings.dev_mode,
        samesite="lax",
        path="/",
    )
    return {"ok": True, "expires_in_seconds": ttl}


@router.get("/session")
async def operator_session(smara_operator: str | None = Cookie(default=None, alias=settings.operator_cookie_name)) -> dict[str, Any]:
    if not settings.operator_secret:
        return {"configured": False, "authenticated": False}
    try:
        _operator_cookie(smara_operator)
    except HTTPException:
        return {"configured": True, "authenticated": False}
    return {"configured": True, "authenticated": True}


@router.delete("/session", status_code=204)
async def operator_sign_out(response: Response) -> None:
    response.delete_cookie(key=settings.operator_cookie_name, path="/")


@router.get("/overview")
async def operator_overview(_: str = Depends(require_operator)) -> dict[str, Any]:
    return {"generated_at": datetime.now().astimezone().isoformat(), "smara": _control_snapshot(), "syntarus": await _syntarus_snapshot()}


@router.get("/syntarus")
async def operator_syntarus(_: str = Depends(require_operator)) -> dict[str, Any]:
    return await _syntarus_snapshot()
