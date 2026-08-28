"""Run a safe, disposable verification against a Smara deployment.

The script is intended to run inside the API container so it can use the
deployment's gateway signing secret without ever printing it. It creates only
one short-lived test task per account and cancels it before exiting.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import secrets
import sys
import time
from typing import Any

import httpx


def signed_headers(secret: str, account_id: str) -> dict[str, str]:
    timestamp = str(int(time.time()))
    signature = hmac.new(secret.encode(), f"{timestamp}.{account_id}".encode(), hashlib.sha256).hexdigest()
    return {
        "X-Smara-Account-Id": account_id,
        "X-Smara-Gateway-Timestamp": timestamp,
        "X-Smara-Gateway-Signature": signature,
    }


def expect(response: httpx.Response, status: int, label: str) -> Any:
    if response.status_code != status:
        raise RuntimeError(f"{label}: expected HTTP {status}, got {response.status_code}: {response.text[:240]}")
    if not response.content:
        return None
    return response.json()


def stream_agent_workflow(
    client: httpx.Client,
    *,
    message: str,
    conversation_id: str,
    expected_tools: set[str] | None = None,
    model_profile: str | None = None,
) -> tuple[str, set[str]]:
    """Consume the same compact SSE contract used by Web, CLI, and Desktop."""
    payload: dict[str, Any] = {
        "message": message,
        "workspace_id": "shadow",
        "conversation_id": conversation_id,
    }
    if model_profile:
        payload["model_profile"] = model_profile
    answer: list[str] = []
    tools: set[str] = set()
    done = False
    with client.stream("POST", "/v1/chat/stream", json=payload, headers={"Accept": "text/event-stream"}) as response:
        if response.status_code != 200:
            raise RuntimeError(f"agent workflow: expected HTTP 200, got {response.status_code}")
        for line in response.iter_lines():
            if not line.startswith("data:"):
                continue
            try:
                event = json.loads(line.removeprefix("data:").strip())
            except json.JSONDecodeError:
                continue
            event_type = event.get("type")
            if event_type == "token":
                answer.append(str(event.get("text", "")))
            elif event_type == "tool_call":
                tools.add(str(event.get("name", "")))
            elif event_type == "error":
                raise RuntimeError(f"agent workflow failed safely: {event.get('kind', 'unknown')}")
            elif event_type == "done":
                done = True
    final = "".join(answer).strip()
    if not done or not final:
        raise RuntimeError("agent workflow ended without a durable done event and visible answer")
    missing = set(expected_tools or set()) - tools
    if missing:
        raise RuntimeError(f"agent workflow omitted expected tools: {sorted(missing)}")
    return final, tools


def verify_desktop_lease_safety() -> str:
    """Exercise the production store's no-replay rule with disposable data."""
    from smara.api import store

    account = f"acct_lease_{secrets.token_hex(5)}"
    try:
        first = store.pair_executor(
            store.create_executor_pairing(account, "Lease smoke A", ["local_terminal"])["code"]
        )
        second = store.pair_executor(
            store.create_executor_pairing(account, "Lease smoke B", ["local_terminal"])["code"]
        )
        task = store.create(
            account,
            "shadow",
            "Desktop lease safety smoke",
            "Never replay an uncertain local command.",
            True,
            [{"name": "desktop.command", "executor_kind": "desktop", "required_capability": "local_terminal"}],
        )
        store.decide(task["id"], account, True, "bounded live safety smoke")
        claimed = store.claim_for_executor(first["executor_id"], first["token"], lease_seconds=1)
        if not claimed:
            raise RuntimeError("desktop lease safety smoke could not claim its disposable step")
        with store._connect() as connection:
            connection.execute(
                "UPDATE task_steps SET lease_expires_at=? WHERE id=?",
                ("2000-01-01T00:00:00+00:00", claimed["step_id"]),
            )
        replay = store.claim_for_executor(second["executor_id"], second["token"])
        failed = store.get(task["id"], account)
        dead_letters = store.dead_letters(account)
        events = store.events(task["id"], account)
        if replay is not None or failed.get("status") != "failed":
            raise RuntimeError("uncertain local side effect was eligible for automatic replay")
        if not any(item.get("step_id") == claimed["step_id"] for item in dead_letters):
            raise RuntimeError("uncertain local side effect did not reach the account dead-letter queue")
        if not any(item.get("type") == "executor.lease_expired_uncertain" for item in events):
            raise RuntimeError("uncertain local side effect did not emit its audit event")
        return "blocked-and-audited"
    finally:
        store.delete_account(account)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.getenv("SMARA_SMOKE_BASE_URL", "http://api:8080"))
    parser.add_argument("--account", default=f"acct_shadow_{secrets.token_hex(5)}")
    parser.add_argument("--rate-limit-burst", type=int, default=0, help="send a bounded authenticated burst and report 429 responses")
    parser.add_argument("--agent-workflows", action="store_true", help="also spend a few live model/search calls on chat, calculator, and cited research")
    parser.add_argument("--model-profile", default="", help="optional hosted profile for live agent workflows")
    parser.add_argument("--desktop-lease-safety", action="store_true", help="verify uncertain local side effects fail closed in the configured task store")
    args = parser.parse_args()
    secret = os.getenv("SMARA_GATEWAY_SIGNING_SECRET")
    if not secret:
        print("FAIL gateway secret is not available in this container", file=sys.stderr)
        return 2

    account = args.account
    other_account = f"acct_shadow_other_{secrets.token_hex(5)}"
    headers = signed_headers(secret, account)
    other_headers = signed_headers(secret, other_account)
    base_url = args.base_url.rstrip("/")
    request_timeout = 90.0 if args.agent_workflows else 20.0
    with httpx.Client(base_url=base_url, timeout=request_timeout, headers=headers) as client:
        health = expect(client.get("/health"), 200, "health")
        ready = expect(client.get("/readyz"), 200, "readyz")
        if args.rate_limit_burst:
            if not 1 <= args.rate_limit_burst <= 150:
                raise RuntimeError("rate-limit burst must be between 1 and 150")
            statuses = [client.get("/v1/tools").status_code for _ in range(args.rate_limit_burst)]
            limited = statuses.count(429)
            print(f"PASS rate-limit review requests={len(statuses)} success={statuses.count(200)} limited={limited}")
            return 0 if limited else 1
        tools = expect(client.get("/v1/tools"), 200, "signed tools")
        integrations = expect(client.get("/v1/integrations"), 200, "integrations")
        if integrations.get("mode") != "local-only":
            raise RuntimeError("hosted personal integrations are not fail-closed")
        tool_items = tools if isinstance(tools, list) else tools.get("tools", [])
        tool_names = {item.get("name") for item in tool_items if isinstance(item, dict)}
        forbidden = {"gmail", "calendar", "github", "drive", "telegram"}
        if any(any(token in str(name).lower() for token in forbidden) for name in tool_names):
            raise RuntimeError("personal integration tool leaked into hosted catalogue")

        created = expect(client.post("/v1/tasks", json={
            "title": "Smara beta shadow check",
            "objective": "Disposable read-only verification task; cancel immediately.",
            "workspace_id": "shadow",
            "requires_approval": True,
            "steps": [{"name": "shadow.check", "executor_kind": "hosted"}],
        }), 201, "create task")
        task_id = created["id"]
        own = expect(client.get(f"/v1/tasks/{task_id}"), 200, "own task")
        with httpx.Client(base_url=base_url, timeout=20.0, headers=other_headers) as other:
            cross_account = other.get(f"/v1/tasks/{task_id}")
        expect(cross_account, 404, "cross-account task isolation")
        cancelled = expect(client.post(f"/v1/tasks/{task_id}/cancel"), 200, "cancel task")
        events = expect(client.get(f"/v1/tasks/{task_id}/events"), 200, "task events")
        workflow_summary = "skipped"
        if args.agent_workflows:
            workflow_ids = [f"smoke_{secrets.token_hex(8)}" for _ in range(3)]
            try:
                direct, direct_tools = stream_agent_workflow(
                    client,
                    message="Reply with exactly SMARA_OK.",
                    conversation_id=workflow_ids[0],
                    expected_tools=set(),
                    model_profile=args.model_profile or None,
                )
                if "SMARA_OK" not in direct:
                    raise RuntimeError("direct agent workflow did not follow the bounded response check")
                calculation, calculation_tools = stream_agent_workflow(
                    client,
                    message="Calculate 144 / 12 and state the result.",
                    conversation_id=workflow_ids[1],
                    expected_tools={"calculate"},
                    model_profile=args.model_profile or None,
                )
                research, research_tools = stream_agent_workflow(
                    client,
                    message="Search the web for the latest Python release, fetch an official source, and cite it.",
                    conversation_id=workflow_ids[2],
                    expected_tools={"research.web_search", "research.fetch_url"},
                    model_profile=args.model_profile or None,
                )
                workflow_summary = (
                    f"chat={len(direct)} calculator={len(calculation)} "
                    f"research={len(research)} tools={len(direct_tools | calculation_tools | research_tools)}"
                )
            finally:
                for conversation_id in workflow_ids:
                    response = client.delete(f"/v1/conversations/{conversation_id}")
                    if response.status_code not in {204, 404}:
                        raise RuntimeError(f"could not clean smoke conversation {conversation_id}")

    lease_status = verify_desktop_lease_safety() if args.desktop_lease_safety else "skipped"
    profile_status = "configured" if os.getenv("SMARA_LLM_PROFILES") else "missing"
    sarvam_status = "configured" if os.getenv("SMARA_SARVAM_KEY") else "not-configured"
    print(
        "PASS live beta shadow "
        f"health={bool(health.get('ok'))} ready={bool(ready.get('ok'))} "
        f"tools={len(tool_names)} integrations=local-only "
        f"task={own.get('status')} cancelled={cancelled.get('status')} "
        f"events={len(events.get('events', []))} "
        f"profiles={profile_status} sarvam={sarvam_status} "
        f"agent_workflows={workflow_summary} desktop_lease={lease_status}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
