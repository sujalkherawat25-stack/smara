"""Bounded real-provider CLI transport probe using only a synthetic question.

This is not a coding/research benchmark or sandbox-isolation certification.
No repository code, user documents or memory notes are sent to the provider.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile

from smara.cli import _load_local_profiles, _resolve_profile_key
from smara.native_provider import ChatEndpoint, ResponsesAdapter
from smara.native_runtime import launch_options, native_binary


class BoundedLiveClient:
    """Test-only request/output ceiling; never fabricate a provider response."""
    def __init__(self, client):
        self.client = client
        self.requests = 0

    def stream(self, method, url, **kwargs):
        if self.requests >= 2:
            raise RuntimeError("Real-provider acceptance request ceiling reached")
        self.requests += 1
        kwargs["json"]["max_tokens"] = 1024
        return self.client.stream(method, url, **kwargs)

    def close(self):
        self.client.close()


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    profiles, active, credentials = _load_local_profiles()
    profile = next(item for item in profiles if item.get("id") == active)
    if profile.get("id") != "sarvam_glm":
        raise SystemExit("This bounded probe requires the configured Sarvam GLM profile; it will not silently use another provider")
    key = _resolve_profile_key(profile, credentials)
    if not key:
        raise SystemExit("Configured model credential is unavailable")
    endpoint = ChatEndpoint(profile["base_url"], profile["model"], key, profile.get("auth_header", "authorization"))
    with tempfile.TemporaryDirectory(prefix="native-live-", dir=root / "build") as temporary:
        scratch = Path(temporary)
        workspace = scratch / "workspace"
        workspace.mkdir()
        with ResponsesAdapter(endpoint) as adapter:
            bounded = BoundedLiveClient(adapter.client)
            adapter.client = bounded
            options, environment = launch_options(adapter, home=scratch / "home", workspace=workspace)
            result = subprocess.run([str(native_binary()), *options, "--no-daemon", "exec", "--skip-git-repo-check", "--json",
                "This is a synthetic text-only transport check. What is 17 multiplied by 23? Answer briefly. Do not call tools, inspect any files, or contact external services."],
                cwd=workspace, env=environment, capture_output=True, text=True, encoding="utf-8", timeout=90,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            events = [json.loads(line) for line in result.stdout.splitlines() if line.strip().startswith("{")]
            answers = [event["item"].get("text", "") for event in events if event.get("type") == "item.completed" and event.get("item", {}).get("type") == "agent_message"]
            passed = result.returncode == 0 and any("391" in answer for answer in answers)
            failures = [{"type": event.get("type"), "error": event.get("error") or event.get("message")} for event in events if event.get("type") in {"error", "turn.failed"}]
            usage = [event.get("usage") for event in events if event.get("type") == "turn.completed"]
            print(json.dumps({"status": "passed" if passed else "failed", "provider": profile["id"], "model": profile["model"],
                "requests": bounded.requests, "request_ceiling": 2, "max_output_tokens_per_request": 1024,
                "exit_code": result.returncode, "answers": answers, "failures": failures, "reported_usage": usage,
                "private_code_sent": False, "coding_quality_tested": False, "os_sandbox_isolation_tested": False}))
            return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
