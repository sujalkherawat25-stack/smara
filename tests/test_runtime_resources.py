import asyncio

import httpx

from smara.agent_runtime import OpenAICompatibleProvider
from smara.config import Settings
from smara.runtime_resources import RuntimeResources


class FakeClient:
    def __init__(self):
        self.calls = 0

    async def post(self, *_args, **_kwargs):
        self.calls += 1
        return httpx.Response(
            200,
            request=httpx.Request("POST", "https://provider.example/v1/chat/completions"),
            json={"choices": [{"message": {"content": "ok"}}]},
        )


def _settings() -> Settings:
    return Settings(
        llm_base_url="https://provider.example/v1",
        llm_api_key="key",
        llm_model="model",
        llm_provider="default",
        syntarus_api_key="",
    )


def test_provider_uses_injected_client_without_opening_or_closing_a_new_pool():
    fake = FakeClient()
    provider = OpenAICompatibleProvider(
        base_url="https://provider.example/v1",
        api_key="key",
        model="model",
        http_client=fake,
    )
    assert asyncio.run(provider.complete(system="system", message="hello")) == "ok"
    assert fake.calls == 1


def test_runtime_resources_parse_profiles_once_and_share_http_client():
    async def exercise():
        resources = await RuntimeResources.create(_settings())
        try:
            assert len(resources.profiles) == 1
            first = resources.runtime(_settings())
            second = resources.runtime(_settings())
            assert first._provider._http_client is resources.http_client
            assert second._provider._http_client is resources.http_client
            assert first._shared_resources is True
        finally:
            await resources.aclose()

    asyncio.run(exercise())
