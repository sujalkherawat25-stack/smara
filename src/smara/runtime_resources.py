"""Process-lifetime resources shared by the hosted Smara API.

The API is a long-lived service.  Keeping transport clients here avoids a new
DNS/TCP/TLS pool on every chat turn while retaining explicit ownership and
shutdown semantics.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

import httpx

from .agent_runtime import OpenAICompatibleProvider, SmaraAgentRuntime
from .config import Settings
from .provider_routing import ModelProfile, load_profiles, resolve_profile
from .syntarus_adapter import SyntarusMemory


@dataclass
class RuntimeResources:
    http_client: httpx.AsyncClient
    syntarus_client: Any | None
    profiles: MappingProxyType

    @classmethod
    async def create(cls, settings: Settings) -> "RuntimeResources":
        timeout = httpx.Timeout(
            45.0,
            connect=5.0,
            read=45.0,
            write=20.0,
            pool=5.0,
        )
        client = httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
            limits=httpx.Limits(max_connections=64, max_keepalive_connections=16, keepalive_expiry=30.0),
            headers={"User-Agent": "Smara/0.1"},
        )
        profiles = load_profiles(
            settings.llm_profiles,
            fallback_base_url=settings.llm_base_url,
            fallback_key=settings.llm_api_key,
            fallback_model=settings.llm_model,
            fallback_provider=settings.llm_provider,
        )
        syntarus_client: Any | None = None
        if settings.syntarus_api_key:
            from syntarus import AsyncMemoryClient

            syntarus_client = AsyncMemoryClient(settings.syntarus_api_key, base_url=settings.syntarus_base_url)
        return cls(client, syntarus_client, MappingProxyType(profiles))

    def resolve_profile(self, settings: Settings, requested: str | None) -> ModelProfile:
        if requested:
            profile = self.profiles.get(requested)
            if profile is None:
                raise ValueError(f"Unknown model profile '{requested}'. Choose one configured by the operator.")
            return profile
        if len(self.profiles) == 1:
            return next(iter(self.profiles.values()))
        return resolve_profile(
            raw=settings.llm_profiles,
            requested=None,
            fallback_base_url=settings.llm_base_url,
            fallback_key=settings.llm_api_key,
            fallback_model=settings.llm_model,
            fallback_provider=settings.llm_provider,
        )

    def runtime(self, settings: Settings, requested: str | None = None) -> SmaraAgentRuntime:
        profile = self.resolve_profile(settings, requested)
        provider = OpenAICompatibleProvider(
            base_url=profile.base_url,
            api_key=profile.api_key,
            model=profile.model,
            auth_header=profile.auth_header,
            http_client=self.http_client,
        )
        provider.capability = profile.capability
        memory = SyntarusMemory(self.syntarus_client) if self.syntarus_client is not None else None
        runtime = SmaraAgentRuntime(provider, memory=memory)
        runtime._shared_resources = True  # type: ignore[attr-defined]
        return runtime

    async def aclose(self) -> None:
        if self.syntarus_client is not None:
            close = getattr(self.syntarus_client, "aclose", None)
            if close is not None:
                await close()
        await self.http_client.aclose()

