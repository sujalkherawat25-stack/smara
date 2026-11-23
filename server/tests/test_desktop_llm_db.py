"""Smara Desktop uses Smara's own model with its owner token: OpenAI-compatible, shared daily budget, no token = no model."""
import json

import httpx
from fastapi import FastAPI
from test_agent_db import ScriptLLM, call, env, one, say  # noqa: F401
from test_desktop_approvals_db import owner_token

from src.bridge.device_api import router as device_router
from src.bridge.llm_proxy import router as llm_router
from src.config import Settings

URL = "/bridge/llm/v1/chat/completions"
BODY = {"model": "anything", "messages": [{"role": "user", "content": "hi"}]}


def api(env):
    app = FastAPI()
    app.state = env.app.state
    app.include_router(device_router)
    app.include_router(llm_router)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://bridge.test")


async def test_no_or_wrong_token_gets_no_model(env):
    async with api(env) as http:
        assert (await http.post(URL, json=BODY)).status_code == 401
        assert (await http.post(URL, json=BODY, headers={"Authorization": "Bearer sma_nope"})).status_code == 401


async def test_an_owner_token_gets_an_openai_style_answer_and_the_usage_is_counted(env):
    async with api(env) as http:
        token, _ = await owner_token(env, http)
        env.app.state.llm = ScriptLLM(say("Namaste"))
        r = await http.post(URL, json=BODY, headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["object"] == "chat.completion" and data["choices"][0]["message"]["content"] == "Namaste"
        assert data["choices"][0]["finish_reason"] == "stop"
        row = await one(env.pool, "select count(*) n from llm_usage where tenant_id = %s and model like 'desktop:%%'", env.t.id)
        assert row["n"] == 1


async def test_tool_calls_and_streaming_come_back_in_the_openai_shape(env):
    async with api(env) as http:
        token, _ = await owner_token(env, http)
        env.app.state.llm = ScriptLLM(call("read_file", path="a.txt"))
        r = await http.post(URL, json={**BODY, "stream": True, "tools": [{"type": "function", "function": {"name": "read_file"}}]},
                            headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        events = [line[6:] for line in r.text.splitlines() if line.startswith("data: ")]
        assert events[-1] == "[DONE]"
        chunk = json.loads(events[0])
        assert chunk["choices"][0]["finish_reason"] == "tool_calls"
        assert chunk["choices"][0]["delta"]["tool_calls"][0]["function"]["name"] == "read_file"


async def test_the_daily_budget_is_shared_with_the_chat(env):
    async with api(env) as http:
        token, _ = await owner_token(env, http)
        env.app.state.settings = Settings(**{**env.app.state.settings.__dict__, "owner_daily_tokens": 1000})
        async with env.pool.connection() as c:              # the owner's WhatsApp/Telegram chat already used it up today
            await c.execute("insert into llm_usage (tenant_id, model, total_tokens) values (%s, 'chat', 1000)", (env.t.id,))
        env.app.state.llm = ScriptLLM(say("no"))
        r = await http.post(URL, json=BODY, headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 429 and "limit" in r.json()["error"]["message"]
