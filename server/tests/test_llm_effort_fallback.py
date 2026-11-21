"""A reasoning model that thinks without end must not leave the owner waiting: ask it to think less, and when a step still comes
back empty hand THAT step to the spare model. Offline: a mock provider records what each call asked for."""
import json

import httpx

from src.loop.llm import LLM, effort_args

BASE = "http://llm.test/v1"


def reply(content="", tool_calls=None, finish="stop"):
    msg = {"role": "assistant", "content": content, **({"tool_calls": tool_calls} if tool_calls else {})}
    return httpx.Response(200, json={"choices": [{"message": msg, "finish_reason": finish}], "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}})


async def make(handler, **kw):
    llm = LLM(BASE, "k", "glm5.3-flash", agent_model="glm5.3", fallback_model="deepseekv4.1-flash-1017", max_backoff_s=0.01, **kw)
    for k in llm._keys:
        await k.http.aclose()
        k.http = httpx.AsyncClient(base_url=BASE, transport=httpx.MockTransport(handler))
    return llm


def test_how_each_model_family_is_asked_to_think_less():
    assert effort_args("glm5.3", "low") == {"reasoning_effort": "low"}
    assert effort_args("glm5.3-flash", "low") == {"reasoning_effort": "low"}
    assert effort_args("deepseekv4.1-flash-1017", "low") == {"thinking": {"type": "disabled"}}
    assert effort_args("some-other-model", "low") == {} and effort_args("glm5.3", "") == {} and effort_args("glm5.3", "high") == {}


async def test_the_agent_step_carries_the_effort_only_when_configured():
    bodies = []

    def handler(req):
        bodies.append(json.loads(req.content))
        return reply("hi")
    llm = await make(handler, reasoning_effort="low")
    await llm.chat([{"role": "user", "content": "x"}], [])
    assert bodies[0]["model"] == "glm5.3" and bodies[0]["reasoning_effort"] == "low" and bodies[0]["max_tokens"] == 8192
    plain = await make(handler)
    await plain.chat([{"role": "user", "content": "x"}], [])
    assert "reasoning_effort" not in bodies[1]


async def test_extraction_is_always_asked_to_think_little_and_has_room_for_its_json():
    bodies = []

    def handler(req):
        bodies.append(json.loads(req.content))
        return reply(json.dumps({"ok": True}))
    llm = await make(handler)
    assert await llm.json("s", "u") == {"ok": True}
    assert bodies[0]["model"] == "glm5.3-flash" and bodies[0]["reasoning_effort"] == "low" and bodies[0]["max_tokens"] == 4096


async def test_a_step_that_stays_empty_after_the_roomier_retry_goes_to_the_spare_model():
    calls = []

    def handler(req):
        b = json.loads(req.content)
        calls.append((b["model"], b["max_tokens"]))
        if b["model"] == "glm5.3":
            return reply("", finish="length")                      # all tokens spent thinking, nothing to show
        return reply("Total 35,500 hai")
    llm = await make(handler, reasoning_effort="low")
    msg = await llm.chat([{"role": "user", "content": "total?"}], [])
    assert msg["content"] == "Total 35,500 hai" and msg["_usage"]["model"] == "deepseekv4.1-flash-1017"
    assert calls == [("glm5.3", 8192), ("glm5.3", 24576), ("deepseekv4.1-flash-1017", 8192)]
    assert llm.stats["fallbacks"] == 1


async def test_without_a_spare_model_an_empty_step_is_returned_as_it_is():
    def handler(req):
        return reply("", finish="length")
    llm = LLM(BASE, "k", "glm5.3", max_backoff_s=0.01)
    for k in llm._keys:
        await k.http.aclose()
        k.http = httpx.AsyncClient(base_url=BASE, transport=httpx.MockTransport(handler))
    msg = await llm.chat([{"role": "user", "content": "x"}], [])
    assert not msg.get("content") and not msg.get("tool_calls")
