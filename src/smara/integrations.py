"""Narrow provider adapters. They accept only explicit, already-approved actions."""
from __future__ import annotations

import base64
import json
from typing import Any

import httpx


class IntegrationExecutor:
    def __init__(self, http: httpx.AsyncClient):
        self.http = http

    async def execute(self, provider: str, action: str, payload: dict[str, Any], secret: str) -> str:
        if provider in {"gmail", "calendar", "drive"}:
            token = self._token(secret)
            return await self._google(provider, action, payload, token)
        if provider == "telegram":
            return await self._telegram(action, payload, secret)
        if provider == "github":
            return await self._github(action, payload, secret)
        raise ValueError("Unsupported integration provider.")

    @staticmethod
    def _token(secret: str) -> str:
        try:
            token = json.loads(secret)["access_token"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise ValueError("Google credential must be JSON containing an access_token.") from exc
        if not isinstance(token, str) or not token:
            raise ValueError("Google access_token is invalid.")
        return token

    async def _google(self, provider: str, action: str, payload: dict[str, Any], token: str) -> str:
        headers = {"Authorization": f"Bearer {token}"}
        if provider == "gmail" and action == "gmail.send":
            for key in ("to", "subject", "text"):
                if not isinstance(payload.get(key), str) or not payload[key]:
                    raise ValueError(f"gmail.send requires {key}.")
            raw = f"To: {payload['to']}\r\nSubject: {payload['subject']}\r\nContent-Type: text/plain; charset=UTF-8\r\n\r\n{payload['text']}"
            encoded = base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")
            response = await self.http.post("https://gmail.googleapis.com/gmail/v1/users/me/messages/send", headers=headers, json={"raw": encoded})
            response.raise_for_status()
            return "Gmail message accepted by provider."
        if provider == "gmail" and action == "gmail.search":
            response = await self.http.get("https://gmail.googleapis.com/gmail/v1/users/me/messages", headers=headers, params={"q": str(payload.get("query", "")), "maxResults": min(int(payload.get("limit", 10)), 20)})
            response.raise_for_status()
            return f"Gmail search returned {len(response.json().get('messages', []))} message references."
        if provider == "calendar" and action == "calendar.create":
            event = payload.get("event")
            if not isinstance(event, dict) or not event.get("summary"):
                raise ValueError("calendar.create requires an event with summary and start/end.")
            calendar_id = str(payload.get("calendar_id", "primary"))
            response = await self.http.post(f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events", headers=headers, json=event)
            response.raise_for_status()
            return "Calendar event created."
        if provider == "calendar" and action == "calendar.list":
            response = await self.http.get("https://www.googleapis.com/calendar/v3/calendars/primary/events", headers=headers, params={"maxResults": min(int(payload.get("limit", 10)), 20), "singleEvents": "true", "orderBy": "startTime"})
            response.raise_for_status()
            return f"Calendar returned {len(response.json().get('items', []))} event references."
        if provider == "drive" and action == "drive.search":
            response = await self.http.get("https://www.googleapis.com/drive/v3/files", headers=headers, params={"q": str(payload.get("query", "")), "pageSize": min(int(payload.get("limit", 10)), 20), "fields": "files(id,name,mimeType)"})
            response.raise_for_status()
            return f"Drive search returned {len(response.json().get('files', []))} file references."
        raise ValueError(f"Unsupported {provider} action: {action}.")

    async def _telegram(self, action: str, payload: dict[str, Any], token: str) -> str:
        if action != "telegram.send" or not payload.get("chat_id") or not isinstance(payload.get("text"), str):
            raise ValueError("telegram.send requires chat_id and text.")
        response = await self.http.post(f"https://api.telegram.org/bot{token}/sendMessage", json={"chat_id": payload["chat_id"], "text": payload["text"]})
        response.raise_for_status()
        if not response.json().get("ok"):
            raise RuntimeError("Telegram rejected the message.")
        return "Telegram message accepted by provider."

    async def _github(self, action: str, payload: dict[str, Any], token: str) -> str:
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if action == "github.list":
            response = await self.http.get("https://api.github.com/user/repos", headers=headers, params={"per_page": min(int(payload.get("limit", 10)), 20)})
            response.raise_for_status()
            return f"GitHub returned {len(response.json())} repository references."
        if action == "github.push":
            for key in ("repo", "path", "content", "message"):
                if not isinstance(payload.get(key), str) or not payload[key]:
                    raise ValueError(f"github.push requires {key}.")
            body = {"message": payload["message"], "content": base64.b64encode(payload["content"].encode()).decode(), "branch": str(payload.get("branch", "main"))}
            if payload.get("sha"):
                body["sha"] = str(payload["sha"])
            response = await self.http.put(f"https://api.github.com/repos/{payload['repo']}/contents/{payload['path']}", headers=headers, json=body)
            response.raise_for_status()
            return "GitHub content commit created."
        raise ValueError(f"Unsupported github action: {action}.")
