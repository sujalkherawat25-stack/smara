"""OAuth authorization-code handoff for Google and GitHub integrations."""
from __future__ import annotations

import base64
import hashlib
import secrets
import time
from urllib.parse import urlencode

import httpx

from .config import settings


GOOGLE_SCOPES = {
    "gmail": ["https://www.googleapis.com/auth/gmail.readonly", "https://www.googleapis.com/auth/gmail.send"],
    "calendar": ["https://www.googleapis.com/auth/calendar"],
    "drive": ["https://www.googleapis.com/auth/drive.metadata.readonly"],
}


def _base64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def begin(provider: str) -> tuple[str, str, str]:
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(48)
    challenge = _base64url(hashlib.sha256(verifier.encode()).digest())
    redirect = f"{settings.public_base_url.rstrip('/')}/v1/integrations/{provider}/oauth/callback"
    if provider in GOOGLE_SCOPES:
        if not settings.google_client_id:
            raise RuntimeError("Google OAuth is not configured on this Smara deployment.")
        query = urlencode({"client_id": settings.google_client_id, "redirect_uri": redirect, "response_type": "code", "scope": " ".join(GOOGLE_SCOPES[provider]), "state": state, "code_challenge": challenge, "code_challenge_method": "S256", "access_type": "offline", "prompt": "consent"})
        return f"https://accounts.google.com/o/oauth2/v2/auth?{query}", state, verifier
    if provider == "github":
        if not settings.github_client_id:
            raise RuntimeError("GitHub OAuth is not configured on this Smara deployment.")
        query = urlencode({"client_id": settings.github_client_id, "redirect_uri": redirect, "scope": "repo read:user", "state": state, "code_challenge": challenge, "code_challenge_method": "S256"})
        return f"https://github.com/login/oauth/authorize?{query}", state, verifier
    raise ValueError("This provider does not support OAuth; configure its token through the authenticated credential endpoint.")


async def exchange(provider: str, code: str, verifier: str) -> dict:
    redirect = f"{settings.public_base_url.rstrip('/')}/v1/integrations/{provider}/oauth/callback"
    async with httpx.AsyncClient(timeout=20) as http:
        if provider in GOOGLE_SCOPES:
            if not settings.google_client_id or not settings.google_client_secret:
                raise RuntimeError("Google OAuth exchange is not configured on this Smara deployment.")
            response = await http.post("https://oauth2.googleapis.com/token", data={"code": code, "client_id": settings.google_client_id, "client_secret": settings.google_client_secret, "redirect_uri": redirect, "grant_type": "authorization_code", "code_verifier": verifier})
        elif provider == "github":
            if not settings.github_client_id or not settings.github_client_secret:
                raise RuntimeError("GitHub OAuth exchange is not configured on this Smara deployment.")
            response = await http.post("https://github.com/login/oauth/access_token", headers={"Accept": "application/json"}, data={"code": code, "client_id": settings.github_client_id, "client_secret": settings.github_client_secret, "redirect_uri": redirect, "code_verifier": verifier})
        else:
            raise ValueError("Unsupported OAuth provider.")
        response.raise_for_status()
        token = response.json()
    if not token.get("access_token"):
        raise RuntimeError("Provider did not return an access token.")
    token["obtained_at"] = int(time.time())
    return token


async def refresh_google(token: dict) -> dict:
    refresh_token = token.get("refresh_token")
    if not isinstance(refresh_token, str) or not refresh_token:
        raise RuntimeError("Google credential has expired and has no refresh token; reconnect the integration.")
    if not settings.google_client_id or not settings.google_client_secret:
        raise RuntimeError("Google token refresh is not configured on this Smara deployment.")
    async with httpx.AsyncClient(timeout=20) as http:
        response = await http.post("https://oauth2.googleapis.com/token", data={"refresh_token": refresh_token, "client_id": settings.google_client_id, "client_secret": settings.google_client_secret, "grant_type": "refresh_token"})
        response.raise_for_status()
        refreshed = response.json()
    if not refreshed.get("access_token"):
        raise RuntimeError("Google token refresh did not return an access token.")
    return {**token, **refreshed, "refresh_token": refreshed.get("refresh_token", refresh_token), "obtained_at": int(time.time())}
