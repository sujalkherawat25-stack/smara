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
import time

import httpx

log = logging.getLogger(__name__)
RETRY_STATUS = {429, 500, 502, 503, 504}


class RateLimited(Exception):
    """The provider kept answering 429/5xx for longer than we are willing to wait."""


def _retry_after(resp: httpx.Response, default: float) -> float:
    ra = resp.headers.get("retry-after", "")
    return float(ra) if ra.replace(".", "", 1).isdigit() else default


class Bucket:
    """Token bucket: `burst` calls at once, then `rpm` per minute. Paces us under the provider's limit
    instead of spending the budget on rejected requests."""

    def __init__(self, rpm: float, burst: int = 4):
        self.rate, self.cap, self.tokens, self.at = rpm / 60.0, float(burst), float(burst), time.monotonic()
        self._lock = asyncio.Lock()

    def wait_needed(self) -> float:
        """Seconds until a call could go out right now (0 = immediately)."""
        if self.rate <= 0:
            return 0.0
        have = min(self.cap, self.tokens + (time.monotonic() - self.at) * self.rate)
        return 0.0 if have >= 1 else (1 - have) / self.rate

    async def take(self) -> None:
        if self.rate <= 0:
            return
        async with self._lock:
            now = time.monotonic()
            self.tokens = min(self.cap, self.tokens + (now - self.at) * self.rate)
            self.at = now
            if self.tokens < 1:
                await asyncio.sleep((1 - self.tokens) / self.rate)
                self.tokens, self.at = 0.0, time.monotonic()
            else:
                self.tokens -= 1


COOLDOWN_DEFAULT_S = 20.0


class _Key:
    """One provider API key with its own HTTP client and pacing bucket. Logs call it key#N, never the key itself."""

    def __init__(self, n: int, http: httpx.AsyncClient, rpm: float):
        self.name = f"key#{n}"
        self.http = http
        self.bucket = Bucket(rpm) if rpm else None
        self.cool_until = 0.0          # monotonic time before which this key is not used (after a 429)
        self.used_seq = 0              # picks go to the least recently used key when two are equally free


class LLM:
    def __init__(self, base_url: str, api_key: str, model: str, reasoning_model: str = "",
                 client: httpx.AsyncClient | None = None, agent_model: str = "",
                 max_retries: int = 8, max_backoff_s: float = 20.0, max_concurrency: int = 4,
                 agent_rpm: float = 0, fallback_model: str = "", max_wait_s: float = 90.0,
                 fallback_after_s: float = 8.0, api_keys: list[str] | None = None):
        self.enabled = bool(base_url)
        self._model, self._reasoning = model, reasoning_model or model
        self.agent_model = agent_model or model     # the tool loop can run on a stronger model than extraction
        self.last_usage: dict = {}
        self.fallback_model, self.max_wait_s, self.fallback_after_s = fallback_model, max_wait_s, fallback_after_s
        self.stats = {"calls": 0, "retries": 0, "fallbacks": 0, "rate_limited": 0}
        self.max_retries, self.max_backoff_s = max_retries, max_backoff_s
        # Bursts queue here instead of colliding with the provider's rate limit (shared with MemoryOS)
        self._slots = asyncio.Semaphore(max_concurrency)
        # A pool of keys: each key has its own rate budget, so N keys serve roughly N times the calls per minute.
        keys = [k.strip() for k in (api_keys or []) if k and k.strip()] or [api_key or ""]
        self._seq = 0
        self._keys: list[_Key] = []
        for i, key in enumerate(keys, start=1):
            http = client or httpx.AsyncClient(
                base_url=base_url, headers={"Authorization": f"Bearer {key}"}, timeout=90)
            self._keys.append(_Key(i, http, agent_rpm))
        if len(self._keys) > 1:
            log.info("LLM key pool: %d keys", len(self._keys))

    def _pick(self, paced: bool) -> _Key:
        """The key that can take a call soonest: not cooling after a 429, least wait on its bucket, least recently used."""
        now = time.monotonic()

        def ready_in(k: _Key) -> float:
            return max(0.0, k.cool_until - now, k.bucket.wait_needed() if paced and k.bucket else 0.0)

        key = min(self._keys, key=lambda k: (ready_in(k), k.used_seq))
        self._seq += 1
        key.used_seq = self._seq
        return key

    def _best_wait(self) -> float:
        """Seconds until the least busy key can pace another agent call (0 = now). Cooldowns are not counted here."""
        waits = [k.bucket.wait_needed() for k in self._keys if k.bucket]
        return min(waits) if waits else 0.0

    async def _post(self, body: dict, max_wait: float | None = None, paced: bool = False) -> dict:
        deadline = time.monotonic() + (max_wait if max_wait is not None else self.max_wait_s)
        self.stats["calls"] += 1
        for attempt in range(self.max_retries + 1):
            key = self._pick(paced)
            if paced and key.bucket:
                await key.bucket.take()
            resp = None
            try:
                async with self._slots:
                    resp = await key.http.post("/chat/completions", json=body)
            except (httpx.TimeoutException, httpx.TransportError):
                if attempt == self.max_retries:
                    raise
            if resp is not None and resp.status_code not in RETRY_STATUS:
                resp.raise_for_status()
                return resp.json()
            if resp is not None and resp.status_code == 429:
                self.stats["rate_limited"] += 1
                key.cool_until = time.monotonic() + _retry_after(resp, COOLDOWN_DEFAULT_S)
                log.warning("LLM 429 on %s; cooling it for %.0fs", key.name, key.cool_until - time.monotonic())
            if attempt == self.max_retries or time.monotonic() > deadline:
                if resp is None:
                    raise RateLimited("provider timed out")
                if resp.status_code in (429, 503):
                    raise RateLimited(f"provider kept returning {resp.status_code}")
                resp.raise_for_status()
            self.stats["retries"] += 1
            if resp is not None and resp.status_code == 429 and any(
                    k is not key and k.cool_until <= time.monotonic() for k in self._keys):
                continue                                   # another key is free: retry on it straight away
            wait = min(self.max_backoff_s, 2 ** attempt + random.random())
            if resp is not None and (ra := resp.headers.get("retry-after", "")).replace(".", "", 1).isdigit():
                wait = min(self.max_backoff_s, float(ra))
            log.warning("LLM %s on %s, retrying in %.1fs (attempt %d)",
                        resp.status_code if resp is not None else "timeout", key.name, wait, attempt + 1)
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

    async def chat(self, messages: list[dict], tools: list[dict], allow_fallback: bool = True) -> dict:
        """One agent step: returns the assistant message (content and/or tool_calls)."""
        req = {"model": self.agent_model, "temperature": 0, "messages": messages}
        if tools:
            req.update(tools=tools, tool_choice="auto")
        fb = bool(self.fallback_model) and self.fallback_model != self.agent_model and allow_fallback
        if any(k.bucket for k in self._keys):
            if fb and self._best_wait() > self.fallback_after_s / 2:
                self.stats["fallbacks"] += 1                    # the main model is busy: do not queue, use the spare one
                body = await self._post({**req, "model": self.fallback_model})
                return self._reply(body, self.fallback_model)
        try:
            # with a fallback model, do not queue behind a rate limit for long: switch after a few seconds
            body = await self._post(req, max_wait=self.fallback_after_s if fb else None, paced=True)
        except RateLimited:
            if not fb:
                raise
            log.warning("agent model %s is rate limited; answering this step with %s", self.agent_model, self.fallback_model)
            self.stats["fallbacks"] += 1
            body = await self._post({**req, "model": self.fallback_model})
            return self._reply(body, self.fallback_model)
        return self._reply(body, self.agent_model)

    def _reply(self, body: dict, model: str) -> dict:
        self.last_usage = body.get("usage") or {}
        msg = dict(body["choices"][0]["message"])
        u = self.last_usage
        msg["_usage"] = {"model": model, "prompt": int(u.get("prompt_tokens") or 0),
                         "completion": int(u.get("completion_tokens") or 0), "total": int(u.get("total_tokens") or 0)}
        return msg

    async def aclose(self) -> None:
        for k in self._keys:
            await k.http.aclose()
