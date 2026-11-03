"""Thin OpenAI-compatible client. Default model for parsing/tool-calling;
`escalate=True` switches to the reasoning model (multi-hop reconciliation only)."""
import json

import httpx


class LLM:
    def __init__(self, base_url: str, api_key: str, model: str, reasoning_model: str = "",
                 client: httpx.AsyncClient | None = None, agent_model: str = ""):
        self.enabled = bool(base_url)
        self._model, self._reasoning = model, reasoning_model or model
        self.agent_model = agent_model or model     # the tool loop can run on a stronger model than extraction
        self.last_usage: dict = {}
        self._http = client or httpx.AsyncClient(
            base_url=base_url, headers={"Authorization": f"Bearer {api_key}"}, timeout=60)

    async def json(self, system: str, user: str | list, *, escalate: bool = False) -> dict:
        """Chat completion constrained to a JSON object. `user` may be multimodal parts."""
        resp = await self._http.post("/chat/completions", json={
            "model": self._reasoning if escalate else self._model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        })
        resp.raise_for_status()
        return json.loads(resp.json()["choices"][0]["message"]["content"])

    async def aclose(self) -> None:
        await self._http.aclose()

    async def chat(self, messages: list[dict], tools: list[dict]) -> dict:
        """One agent step: returns the assistant message (content and/or tool_calls)."""
        resp = await self._http.post("/chat/completions", json={
            "model": self.agent_model, "temperature": 0, "messages": messages,
            "tools": tools, "tool_choice": "auto",
        })
        resp.raise_for_status()
        body = resp.json()
        self.last_usage = body.get("usage") or {}
        return body["choices"][0]["message"]
