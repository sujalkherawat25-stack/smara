"""LLM key pool, offline: httpx.MockTransport stands in for the provider and records which key each call used."""
import json

import httpx
import pytest

from src.config import Settings
from src.loop.llm import LLM, RateLimited

BASE = "http://llm.test/v1"
OK_BODY = {"choices": [{"message": {"content": json.dumps({"ok": True})}}], "usage": {"total_tokens": 5}}


def ok():
    return httpx.Response(200, json=OK_BODY)


async def make_llm(keys, handler, **kw):
    """An LLM whose per-key HTTP clients all talk to `handler` (the default clients would reach the network)."""
    llm = LLM(BASE, "", "m", api_keys=keys, max_backoff_s=0.01, **kw)
    for k in llm._keys:
        await k.http.aclose()
        k.http = httpx.AsyncClient(base_url=BASE, headers={"Authorization": f"Bearer {keys[llm._keys.index(k)]}"},
                                   transport=httpx.MockTransport(handler))
    return llm


async def test_two_keys_alternate_when_both_are_free():
    seen = []

    def handler(req):
        seen.append(req.headers["authorization"])
        return ok()

    llm = await make_llm(["k-one", "k-two"], handler)
    for _ in range(4):
        assert await llm.json("system", "user") == {"ok": True}
    assert seen == ["Bearer k-one", "Bearer k-two", "Bearer k-one", "Bearer k-two"]
    await llm.aclose()


async def test_429_on_one_key_is_retried_on_the_other_and_that_key_cools_down():
    seen = []

    def handler(req):
        seen.append(req.headers["authorization"])
        if req.headers["authorization"] == "Bearer k-one":
            return httpx.Response(429, headers={"retry-after": "30"})
        return ok()

    llm = await make_llm(["k-one", "k-two"], handler)
    assert await llm.json("system", "user") == {"ok": True}      # key 1 refused, key 2 answered in the same call
    assert await llm.json("system", "user") == {"ok": True}      # key 1 is cooling for 30s, so key 2 again
    assert seen == ["Bearer k-one", "Bearer k-two", "Bearer k-two"]
    assert llm.stats["rate_limited"] == 1
    await llm.aclose()


async def test_all_keys_rate_limited_raises_rate_limited():
    seen = []

    def handler(req):
        seen.append(req.headers["authorization"])
        return httpx.Response(429, headers={"retry-after": "0"})

    llm = await make_llm(["k-one", "k-two"], handler, max_retries=2)
    with pytest.raises(RateLimited):
        await llm.json("system", "user")
    assert set(seen) == {"Bearer k-one", "Bearer k-two"}         # both keys were tried before giving up
    await llm.aclose()


async def test_single_key_still_works_and_is_named_key_1():
    llm = await make_llm(["only-key"], lambda req: ok())
    assert len(llm._keys) == 1 and llm._keys[0].name == "key#1"
    assert await llm.json("system", "user") == {"ok": True}
    await llm.aclose()


async def test_agent_chat_paces_each_key_with_its_own_bucket():
    seen = []

    def handler(req):
        seen.append(req.headers["authorization"])
        return ok()

    llm = await make_llm(["k-one", "k-two"], handler, agent_rpm=600)    # burst of 4 per key
    for _ in range(3):
        msg = await llm.chat([{"role": "user", "content": "hi"}], [])
        assert msg["_usage"]["total"] == 5
    assert seen == ["Bearer k-one", "Bearer k-two", "Bearer k-one"]
    await llm.aclose()


def test_settings_key_pool_from_env_style_values():
    assert Settings(llm_api_key="solo").llm_keys() == ["solo"]
    assert Settings(llm_api_key="solo", llm_api_keys="a, b ,a,").llm_keys() == ["a", "b"]   # pool wins, duplicates dropped
    assert Settings().llm_keys() == []
