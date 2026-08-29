"""Native Smara web authentication and account/Telegram linking routes.

This module deliberately has no dependency on ``backend.app.memento``.  It
uses the same account tables so memories retain their account namespace, but
Smara now owns the browser session and the Telegram link lifecycle.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from fastapi import APIRouter, Cookie, Header, HTTPException, Request, Response

from .auth_store import AccountStore
from .config import settings

LOG = logging.getLogger("smara.auth")
router = APIRouter(prefix="/v1/auth", tags=["auth"])

COOKIE_MAX_AGE = max(3600, max(1, settings.session_ttl_days) * 86400)


def _store() -> AccountStore:
    # Keep the fallback useful for local desktop development. Production
    # deployments set SMARA_ACCOUNTS_DATABASE_URL to the existing account DB.
    return AccountStore(settings.accounts_database_url, settings.database_path)


account_store = _store()
try:
    account_store.ensure_schema()
except Exception:
    # Do not make imports fail in a local read-only/test environment. The
    # first authenticated operation will return an actionable 503 instead.
    LOG.exception("native Smara account schema is unavailable")


@dataclass(frozen=True)
class GoogleClaims:
    subject: str
    email: str
    display_name: str | None
    avatar_url: str | None


async def verify_google_id_token(id_token: str) -> GoogleClaims:
    """Verify a Google GIS token against Google's signing keys and audience."""
    if not settings.google_client_id:
        raise HTTPException(503, "Google sign-in is not configured on Smara.")
    if not id_token or len(id_token) > 16_000:
        raise HTTPException(400, "Google sign-in token is missing or invalid.")

    def verify() -> dict[str, Any]:
        try:
            from google.auth.transport import requests as google_requests
            from google.oauth2 import id_token as google_id_token
        except ImportError as exc:  # pragma: no cover - packaging guard
            raise RuntimeError("Google sign-in support is not installed on Smara.") from exc
        return google_id_token.verify_oauth2_token(
            id_token,
            google_requests.Request(),
            settings.google_client_id,
        )

    try:
        claims = await asyncio.to_thread(verify)
    except HTTPException:
        raise
    except Exception as exc:
        LOG.info("google token rejected type=%s", type(exc).__name__)
        raise HTTPException(401, "Google sign-in could not be verified. Try again.") from exc
    subject = str(claims.get("sub") or "").strip()
    email = str(claims.get("email") or "").strip().lower()
    if not subject or not email or claims.get("email_verified") is False:
        raise HTTPException(401, "Your Google account did not provide a verified email.")
    return GoogleClaims(subject, email, str(claims.get("name") or "").strip() or None, str(claims.get("picture") or "").strip() or None)


def account_public(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "account_id": row["id"],
        "email": row.get("email"),
        "display_name": row.get("display_name"),
        "avatar_url": row.get("avatar_url"),
        "plan": row.get("plan") or "free",
    }


def issue_session(account_id: str, *, user_agent: str | None = None) -> tuple[str, datetime]:
    if not settings.session_secret:
        raise HTTPException(503, "Smara session signing is not configured.")
    now = datetime.now(timezone.utc)
    expires = now + timedelta(seconds=COOKIE_MAX_AGE)
    token_id = f"sess_{uuid.uuid4().hex}"
    token = jwt.encode(
        {"sub": account_id, "jti": token_id, "iat": int(now.timestamp()), "exp": int(expires.timestamp()), "aud": "smara-web", "iss": "smara-api"},
        settings.session_secret,
        algorithm="HS256",
    )
    try:
        account_store.create_session(token_id, account_id, expires, user_agent)
    except Exception as exc:
        LOG.exception("could not persist Smara session")
        raise HTTPException(503, "Smara could not save your session. Try again.") from exc
    return token, expires


def verify_session_cookie(token: str | None) -> str | None:
    """Return an active account id, or None for an absent/invalid cookie."""
    if not token or not settings.session_secret:
        return None
    try:
        claims = jwt.decode(token, settings.session_secret, algorithms=["HS256"], audience="smara-web", issuer="smara-api", options={"require": ["sub", "jti", "iat", "exp", "aud", "iss"]})
        account_id = claims.get("sub")
        token_id = claims.get("jti")
        if not isinstance(account_id, str) or not account_id.startswith("acct_") or not isinstance(token_id, str):
            return None
        return account_id if account_store.session_account(token_id, account_id) else None
    except (jwt.InvalidTokenError, OSError, ValueError):
        return None
    except Exception:
        LOG.exception("Smara session lookup failed")
        return None


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=settings.auth_cookie_name,
        value=token,
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(key=settings.auth_cookie_name, path="/")


def current_native_account(cookie: str | None) -> dict[str, Any] | None:
    account_id = verify_session_cookie(cookie)
    if not account_id:
        return None
    try:
        return account_store.account_by_id(account_id)
    except Exception:
        LOG.exception("native account lookup failed")
        return None


def require_native_account(cookie: str | None) -> dict[str, Any]:
    account = current_native_account(cookie)
    if not account:
        raise HTTPException(401, "Sign in to Smara first.")
    return account


@router.get("/config")
async def auth_config() -> dict[str, Any]:
    return {"google_client_id": settings.google_client_id, "email_sign_in": False, "session_cookie": settings.auth_cookie_name}


@router.post("/google")
async def google_sign_in(body: dict[str, Any], response: Response, request: Request) -> dict[str, Any]:
    claims = await verify_google_id_token(str(body.get("id_token") or ""))
    try:
        account = account_store.upsert_google_account(google_sub=claims.subject, email=claims.email, display_name=claims.display_name, avatar_url=claims.avatar_url)
        token, _ = issue_session(account["id"], user_agent=request.headers.get("user-agent"))
    except HTTPException:
        raise
    except Exception as exc:
        LOG.exception("could not create native account")
        raise HTTPException(503, "Smara could not finish sign-in. Try again.") from exc
    set_session_cookie(response, token)
    return account_public(account)


@router.get("/me")
async def current_user(smara_session: str | None = Cookie(default=None, alias=settings.auth_cookie_name)) -> dict[str, Any]:
    return account_public(require_native_account(smara_session))


@router.post("/logout")
async def logout(response: Response, smara_session: str | None = Cookie(default=None, alias=settings.auth_cookie_name)) -> dict[str, bool]:
    if smara_session and settings.session_secret:
        try:
            claims = jwt.decode(smara_session, settings.session_secret, algorithms=["HS256"], options={"verify_exp": False, "verify_aud": False, "verify_iss": False})
            if isinstance(claims.get("jti"), str):
                account_store.delete_session(claims["jti"])
        except Exception:
            pass
    clear_session_cookie(response)
    return {"ok": True}


@router.post("/email/request")
async def email_request() -> None:
    raise HTTPException(501, "Email sign-in is not enabled on native Smara yet. Use Google sign-in.")


@router.post("/email/verify")
async def email_verify() -> None:
    raise HTTPException(501, "Email sign-in is not enabled on native Smara yet. Use Google sign-in.")


@router.get("/link/telegram")
async def create_telegram_link(smara_session: str | None = Cookie(default=None, alias=settings.auth_cookie_name)) -> dict[str, Any]:
    account = require_native_account(smara_session)
    try:
        code = account_store.create_telegram_code(account["id"])
    except Exception as exc:
        raise HTTPException(503, "Telegram linking is temporarily unavailable.") from exc
    return {**code, "bot_url": settings.telegram_bot_url}


@router.get("/link/telegram/status")
async def telegram_status(smara_session: str | None = Cookie(default=None, alias=settings.auth_cookie_name)) -> dict[str, Any]:
    account = require_native_account(smara_session)
    try:
        return account_store.telegram_status(account["id"])
    except Exception as exc:
        raise HTTPException(503, "Telegram status is temporarily unavailable.") from exc


@router.delete("/link/telegram")
async def unlink_telegram(smara_session: str | None = Cookie(default=None, alias=settings.auth_cookie_name)) -> dict[str, bool]:
    account = require_native_account(smara_session)
    try:
        account_store.unlink_telegram(account["id"])
    except Exception as exc:
        raise HTTPException(503, "Telegram unlink is temporarily unavailable.") from exc
    return {"ok": True}

