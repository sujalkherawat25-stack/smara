"""Run a safe, disposable verification against a Smara deployment.

The script is intended to run inside the API container so it can use the
deployment's gateway signing secret without ever printing it. It creates only
one short-lived test task per account and cancels it before exiting.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.getenv("SMARA_SMOKE_BASE_URL", "http://api:8000"))
    parser.add_argument("--account", default=f"acct_shadow_{secrets.token_hex(5)}")
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
    with httpx.Client(base_url=base_url, timeout=20.0, headers=headers) as client:
        health = expect(client.get("/health"), 200, "health")
        ready = expect(client.get("/readyz"), 200, "readyz")
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

    profile_status = "configured" if os.getenv("SMARA_LLM_PROFILES") else "missing"
    sarvam_status = "configured" if os.getenv("SMARA_SARVAM_KEY") else "not-configured"
    print(
        "PASS live beta shadow "
        f"health={bool(health.get('ok'))} ready={bool(ready.get('ok'))} "
        f"tools={len(tool_names)} integrations=local-only "
        f"task={own.get('status')} cancelled={cancelled.get('status')} "
        f"events={len(events.get('events', []))} "
        f"profiles={profile_status} sarvam={sarvam_status}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
