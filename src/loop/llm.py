"""Thin OpenAI-compatible client. `model` for quick extraction, `agent_model` for
the tool loop, `escalate=True` switches extraction to the reasoning model.

Rate limits (429) and provider hiccups (5xx, timeouts) are retried with
exponential backoff, honouring Retry-After, so a burst of traffic slows a
reply down instead of failing it.
"""
import asyncio
import json
import logging
import random

import httpx

log = logging.getLogger(__name__)
RETRY_STATUS = {429, 500, 502, 503, 504}


class LLM:
    def __init__(self, base_url: str, api_key: str, model: str, reasoning_model: str = "",
                 client: httpx.AsyncClient | None = None, agent_model: str = "",
                 max_retries: int = 5, max_backoff_s: float = 20.0):
        self.enabled = bool(base_url)
        self._model, self._reasoning = model, reasoning_model or model
        self.agent_model = agent_model or model     # the tool loop can run on a stronger model than extraction
        self.last_usage: dict = {}
        self.max_retries, self.max_backoff_s = max_retries, max_backoff_s
        self._http = client or httpx.AsyncClient(
            base_url=base_url, headers={"Authorization": f"Bearer {api_key}"}, timeout=90)

    async def _post(self, body: dict) -> dict:
        for attempt in range(self.max_retries + 1):
            try:
                resp = await self._http.post("/chat/completions", json=body)
            except (httpx.TimeoutException, httpx.TransportError):
                if attempt == self.max_retries:
                    raise
                resp = None
            if resp is not None and resp.status_code not in RETRY_STATUS:
                resp.raise_for_status()
                return resp.json()
            if attempt == self.max_retries:
                resp.raise_for_status()
            wait = min(self.max_backoff_s, 2 ** attempt + random.random())
            if resp is not None and (ra := resp.headers.get("retry-after", "")).replace(".", "", 1).isdigit():
                wait = min(self.max_backoff_s, float(ra))
            log.warning("LLM %s, retrying in %.1fs (attempt %d)", resp.status_code if resp is not None else "timeout",
                        wait, attempt + 1)
            await asyncio.sleep(wait)
        raise RuntimeError("unreachable")

    async def json(self, system: str, user: str | list, *, escalate: bool = False) -> dict:
        """Chat completion constrained to a JSON object. `user` may be multimodal parts."""
        body = await self._post({
            "model": self._reasoning if escalate else self._model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        })
        return json.loads(body["choices"][0]["message"]["content"])

    async def chat(self, messages: list[dict], tools: list[dict]) -> dict:
        """One agent step: returns the assistant message (content and/or tool_calls)."""
        body = await self._post({"model": self.agent_model, "temperature": 0, "messages": messages,
                                 "tools": tools, "tool_choice": "auto"})
        self.last_usage = body.get("usage") or {}
        return body["choices"][0]["message"]

    async def aclose(self) -> None:
        await self._http.aclose()
