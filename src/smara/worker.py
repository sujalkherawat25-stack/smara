from __future__ import annotations

import asyncio
import json
import time
import httpx

from syntarus import AsyncMemoryClient
from .config import settings
from .store import TaskStore, open_task_store
from .syntarus_adapter import SyntarusMemory
from .research import OpenAIResearchSynthesizer, ResearchExecutor
from .sandbox import run as run_sandbox, run_remote as run_remote_sandbox
from .agent_runtime import OpenAICompatibleProvider
from .agent_step import BoundedAgentStepRuntime
from .tool_registry import ToolContext, default_tool_registry
from .integrations import IntegrationExecutor
from .vault import SecretVault
from .integration_oauth import refresh_google
from .capture_processing import process_capture
from .provider_routing import resolve_profile
from . import llm_errors
from .work_signals import wait_for_signal


async def _memory_context(memory: SyntarusMemory | None, task: dict, store: TaskStore) -> str:
    """Memory enriches a task but never prevents the task from running."""
    if memory is None:
        return ""
    try:
        return await memory.context_for_task(task)
    except Exception:
        store.append_event(task["id"], "memory.unavailable", json.dumps({"operation": "search"}))
        return ""


async def _memory_write(memory: SyntarusMemory | None, task: dict, store: TaskStore, operation: str, **kwargs: object) -> None:
    """Record memory write outages without turning a completed task into a retry."""
    if memory is None:
        return
    try:
        if operation == "research":
            await memory.remember_verified_research(task, kwargs["report"], int(kwargs["evidence_count"]))
        else:
            await memory.remember_completion(task, str(kwargs["result"]))
    except Exception:
        store.append_event(task["id"], "memory.unavailable", json.dumps({"operation": "write"}))


async def run_once(store: TaskStore, memory: SyntarusMemory | None, *, sandbox_enabled: bool | None = None) -> bool:
    # ``None`` preserves local tests/development; the long-lived worker passes
    # the explicit deployment setting, which defaults to false.
    allowed = ("hosted", "sandbox") if sandbox_enabled is not False else ("hosted",)
    task = store.claim_one(executor_kinds=allowed)
    if task is None:
        return False
    if task["status"] == "waiting_approval":
        return True
    try:
        context = ""
        context = await _memory_context(memory, task, store)
        if task["name"].startswith("research."):
            synthesizer = None
            if settings.research_synthesis_enabled:
                # Use the same operator-selected profile as agent.execute.
                # A profiles-only deployment must not silently lose research
                # synthesis or accidentally use a different provider.
                try:
                    profile = resolve_profile(
                        raw=settings.llm_profiles,
                        requested=settings.llm_default_profile or None,
                        fallback_base_url=settings.llm_base_url,
                        fallback_key=settings.llm_api_key,
                        fallback_model=settings.llm_model,
                        fallback_provider=settings.llm_provider,
                    )
                except ValueError:
                    profile = None
                if profile is not None:
                    synthesizer = OpenAIResearchSynthesizer(
                        base_url=profile.base_url,
                        api_key=profile.api_key,
                        model=profile.model,
                        auth_header=profile.auth_header,
                    )
            outcome = await ResearchExecutor(store, synthesizer=synthesizer).run_step(task)
            if outcome.report:
                await _memory_write(memory, task, store, "research", report=outcome.report, evidence_count=outcome.verified_evidence_count)
            store.complete_step(task["step_id"], task["account_id"], outcome.text)
            return True
        if task["name"] == "capture.process":
            async with httpx.AsyncClient(timeout=httpx.Timeout(20.0), follow_redirects=False) as client:
                result = await process_capture(
                    store,
                    task,
                    client,
                    transcription_base_url=settings.capture_transcription_base_url,
                    transcription_api_key=settings.capture_transcription_api_key,
                    transcription_model=settings.capture_transcription_model,
                    transcription_auth_header=settings.capture_transcription_auth_header,
                    vision_base_url=settings.capture_vision_base_url,
                    vision_api_key=settings.capture_vision_api_key,
                    vision_model=settings.capture_vision_model,
                    vision_auth_header=settings.capture_vision_auth_header,
                    ocr_base_url=settings.capture_ocr_base_url,
                    ocr_api_key=settings.capture_ocr_api_key,
                    ocr_model=settings.capture_ocr_model,
                    ocr_language=settings.capture_ocr_language,
                    ocr_auth_header=settings.capture_ocr_auth_header,
                )
            store.complete_step(task["step_id"], task["account_id"], result)
            return True
        if task["name"] in {"agent.execute", "execute_task"}:
            requested_profile = None
            raw_payload = task.get("executor_payload")
            if isinstance(raw_payload, str):
                try:
                    raw_payload = json.loads(raw_payload)
                except json.JSONDecodeError:
                    raw_payload = None
            if isinstance(raw_payload, dict) and isinstance(raw_payload.get("model_profile"), str):
                requested_profile = raw_payload["model_profile"]
            profile = resolve_profile(
                raw=settings.llm_profiles,
                requested=requested_profile or settings.llm_default_profile or None,
                fallback_base_url=settings.llm_base_url,
                fallback_key=settings.llm_api_key,
                fallback_model=settings.llm_model,
                fallback_provider=settings.llm_provider,
            )
            provider = OpenAICompatibleProvider(
                base_url=profile.base_url,
                api_key=profile.api_key,
                model=profile.model,
                auth_header=profile.auth_header,
            )
            with_client = httpx.AsyncClient(timeout=httpx.Timeout(12.0), follow_redirects=False)
            async with with_client as client:
                async def integration_runner(provider_name: str, action: str, payload: dict) -> str:
                    if not settings.integration_master_keys:
                        raise RuntimeError("Integration credentials are not configured on this worker.")
                    connection = store.integration(task["account_id"], provider_name)
                    credential = store.encrypted_integration_credential(connection["id"])
                    secret = SecretVault(settings.integration_master_keys).decrypt(credential["encrypted_secret"])
                    if provider_name in {"gmail", "calendar", "drive"}:
                        token = json.loads(secret)
                        expires_in = int(token.get("expires_in", 3600))
                        if int(token.get("obtained_at", 0)) + expires_in - 60 <= int(time.time()):
                            token = await refresh_google(token)
                            secret = json.dumps(token)
                            store.store_integration_credential(task["account_id"], provider_name, "oauth_token", SecretVault(settings.integration_master_keys).encrypt(secret))
                    return await IntegrationExecutor(client).execute(provider_name, action, payload, secret)

                def integration_requester(provider_name: str, action: str, preview: str, idempotency_key: str, payload: dict) -> dict:
                    result = store.request_integration_action(task["account_id"], provider_name, action, preview, idempotency_key, payload)
                    record("agent.approval_requested", {"provider": provider_name, "action": action, "status": result.get("status"), "action_id": result.get("id")})
                    return result

                def record(event_type: str, payload: dict) -> None:
                    store.append_event(task["id"], event_type, json.dumps(payload, ensure_ascii=False)[:2_000])
                def desktop_requester(capability: str, preview: str, payload: dict) -> dict:
                    child = store.create(
                        task["account_id"], task["workspace_id"], f"Desktop: {preview[:80]}", preview,
                        True,
                        [{"name": f"desktop.{capability}", "executor_kind": "desktop", "required_capability": capability, "executor_payload": payload}],
                    )
                    # Surface the approval immediately. The parent agent is
                    # already leased, so the normal worker claim transition
                    # cannot be relied on to make this child visible.
                    child = store.request_approval(child["id"], task["account_id"])
                    record("agent.desktop_task_requested", {"task_id": child["id"], "capability": capability})
                    return {"task_id": child["id"], "status": child["status"], "capability": capability}

                # In the default local-only security posture, the hosted VM
                # never receives or decrypts a user's Gmail/Calendar/Drive/
                # GitHub/Telegram credential. Personal integrations will be
                # dispatched to a future paired local adapter instead.
                hosted_runner = integration_runner if settings.hosted_user_integrations_enabled else None
                hosted_requester = integration_requester if settings.hosted_user_integrations_enabled else None
                result = await BoundedAgentStepRuntime(provider, default_tool_registry(
                    client,
                    integration_runner=hosted_runner,
                    integration_requester=hosted_requester,
                    desktop_requester=desktop_requester,
                    include_user_integrations=settings.hosted_user_integrations_enabled,
                )).run(
                    task=task,
                    memory_context=context,
                    tool_context=ToolContext(task["account_id"], task["workspace_id"], client, hosted_runner, hosted_requester, desktop_requester),
                    event_hook=record,
                )
            await _memory_write(memory, task, store, "completion", result=result.text)
            store.complete_step(task["step_id"], task["account_id"], result.text)
            return True
        if task.get("executor_kind") == "sandbox":
            # Sandbox tasks must begin at a visible approval gate. The task
            # engine never accepts a pre-approved arbitrary command.
            if not store.task_is_approved(task["id"], task["account_id"]):
                raise RuntimeError("Sandbox execution requires an approval-gated task.")
            payload = task.get("executor_payload") or {}
            if isinstance(payload, str):
                payload = json.loads(payload)
            command = payload.get("command")
            if not isinstance(command, str):
                raise RuntimeError("Sandbox step requires a command payload.")
            if settings.sandbox_url and settings.sandbox_token:
                result = await run_remote_sandbox(settings.sandbox_url, settings.sandbox_token, command)
            else:
                # Local tests may inject sandbox_enabled=True and monkeypatch
                # this bounded recipe. The long-lived production worker only
                # enables claims when a remote service is configured below.
                result = run_sandbox(command)
            store.complete_step(task["step_id"], task["account_id"], result)
            return True
    # Executor integration is intentionally explicit. This worker has no
    # implicit shell/browser privileges; a registered executor will replace
    # this deterministic safe completion step.
        result = "Task accepted by Smara. Executor integration is required to perform external actions."
        if context:
            result += " Relevant shared memory was retrieved."
        await _memory_write(memory, task, store, "completion", result=result)
        store.complete_step(task["step_id"], task["account_id"], result)
    except Exception as exc:
        kind = llm_errors.classify(exc) if task.get("name") in {"agent.execute", "execute_task"} else llm_errors.KIND_UNKNOWN
        if kind != llm_errors.KIND_UNKNOWN:
            safe_error = llm_errors.user_message(kind, provider=settings.llm_provider)
            retryable = kind in {
                llm_errors.KIND_RATE_LIMIT,
                llm_errors.KIND_PROVIDER_DOWN,
                llm_errors.KIND_TIMEOUT,
                llm_errors.KIND_NETWORK,
            }
        else:
            safe_error = str(exc)
            retryable = True
        store.fail_step(task["step_id"], task["account_id"], safe_error, retryable=retryable)
    return True


async def main() -> None:
    store = open_task_store(database_url=settings.database_url, database_path=settings.database_path, redis_url=settings.redis_url if settings.work_signals_enabled else "")
    memory = None
    if settings.syntarus_api_key:
        memory = SyntarusMemory(AsyncMemoryClient(settings.syntarus_api_key, base_url=settings.syntarus_base_url))
    try:
        concurrency = settings.worker_concurrency
        while True:
            worked = await asyncio.gather(*(
                run_once(store, memory, sandbox_enabled=settings.sandbox_enabled and bool(settings.sandbox_url and settings.sandbox_token))
                for _ in range(concurrency)
            ))
            if not any(worked):
                await wait_for_signal(settings.redis_url, 5, enabled=settings.work_signals_enabled)
            else:
                await asyncio.sleep(0)
    finally:
        if memory and hasattr(memory._client, "aclose"):
            await memory._client.aclose()
        close_store = getattr(store, "close", None)
        if close_store is not None:
            close_store()


if __name__ == "__main__":
    asyncio.run(main())
