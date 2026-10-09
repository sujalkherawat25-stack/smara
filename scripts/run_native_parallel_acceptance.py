"""Bounded, real-model native worker probe on two synthetic read-only files.

Not a certification of worktree isolation, crash recovery or business autonomy.
"""
from __future__ import annotations

from contextlib import contextmanager
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time
import uuid

from smara.cli import _load_local_profiles, _resolve_profile_key
from smara.native_provider import ChatEndpoint, ResponsesAdapter
from smara.native_runtime import active_profile, launch_options, native_binary
from scripts.run_native_coding_acceptance import BoundedCodingClient, cleanup_scratch, parse_cli_output


class ConcurrentProbeClient(BoundedCodingClient):
    def __init__(self, client):
        super().__init__(client, ceiling=16)
        self.active_streams = 0
        self.peak_concurrent_streams = 0
        self.concurrent_lock = threading.Lock()

    @contextmanager
    def stream(self, *args, **kwargs):
        upstream = super().stream(*args, **kwargs)
        with self.concurrent_lock:
            self.active_streams += 1
            self.peak_concurrent_streams = max(self.peak_concurrent_streams, self.active_streams)
        try:
            with upstream as response:
                yield response
        finally:
            with self.concurrent_lock:
                self.active_streams -= 1


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, default=root / "build" / "native-parallel-2026-10-08.json")
    args = parser.parse_args()
    report_path = args.report.resolve()
    if not report_path.is_relative_to(root / "build"):
        raise SystemExit("Acceptance reports must stay under build/")
    profiles, selected, credentials = _load_local_profiles()
    profile = active_profile(profiles, selected)
    if profile["id"] != "sarvam_glm":
        raise SystemExit("Select Sarvam GLM explicitly for this acceptance")
    key = _resolve_profile_key(profile, credentials)
    if not key:
        raise SystemExit("Configured model credential unavailable")
    scratch = (root / "build" / ("native-coding-parallel-" + uuid.uuid4().hex)).resolve()
    assert scratch.is_relative_to((root / "build").resolve())
    scratch.mkdir()
    workspace = scratch / "workspace"
    workspace.mkdir()
    filenames = ("design-notes.txt", "constraints.txt")
    for filename in filenames:
        shutil.copy2(root / "tests" / "fixtures" / "native_coding" / filename, workspace / filename)
    initial = {name: hashlib.sha256((workspace / name).read_bytes()).hexdigest() for name in filenames}
    prompt = (
        "This is a disposable synthetic read-only worker test. Delegate one worker to "
        "inspect design-notes.txt and another worker to inspect constraints.txt. "
        "Launch both workers before waiting for their results, then collect both results "
        "and briefly explain what the team should build next and what must be verified first. "
        "Include the project name stated in the design notes in your final recommendation. "
        "Use exactly two workers, without nested delegation. Do not read the files yourself "
        "instead of delegating. Neither you nor the workers may modify any file, ask for "
        "elevated execution, install packages, access other projects or external services."
    )
    started = time.monotonic()
    report = None
    with ResponsesAdapter(ChatEndpoint(profile["base_url"], profile["model"], key, profile.get("auth_header", "authorization"))) as adapter:
        bounded = ConcurrentProbeClient(adapter.client)
        adapter.client = bounded
        options, environment = launch_options(adapter, home=scratch / "home", workspace=workspace)
        try:
            process = subprocess.run([str(native_binary()), *options,
                "-c", 'sandbox_mode="read-only"', "-c", "agents.max_concurrent_threads_per_session=2",
                "-c", "agents.max_depth=1", "--no-daemon", "exec", "--skip-git-repo-check", "--json", prompt],
                cwd=workspace, env=environment, capture_output=True, text=True, encoding="utf-8", timeout=240,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            items, completed, failures = parse_cli_output(process)
            calls = [item for item in items if item.get("type") == "collab_tool_call"]
            spawns = [item for item in calls if item["tool"] == "spawn_agent" and item["status"] == "completed"]
            children = {identity for item in spawns for identity in item["receiver_thread_ids"]}
            finished_children = {identity for item in calls for identity, state in item.get("agents_states", {}).items() if state.get("status") == "completed"}
            answers = [item["text"] for item in items if item.get("type") == "agent_message"]
            unchanged = all((workspace / name).is_file() and hashlib.sha256((workspace / name).read_bytes()).hexdigest() == initial[name] for name in filenames)
            checks = {
                "native_turn_completed": completed and process.returncode == 0,
                "two_actual_native_workers_spawned": len(children) == 2,
                "both_worker_results_collected": children.issubset(finished_children) and bool(children),
                "overlapping_provider_streams_observed": bounded.peak_concurrent_streams >= 2,
                "answer_uses_both_inputs": any("Harrier" in answer and "rollback" in answer.lower() for answer in answers),
                "synthetic_inputs_unchanged": unchanged,
                "no_native_errors": not failures,
            }
            report = {"status": "passed" if all(checks.values()) else "failed", "checks": checks,
                "provider_requests": bounded.requests, "request_ceiling": bounded.ceiling,
                "max_output_tokens_per_request": 2048, "peak_concurrent_provider_streams": bounded.peak_concurrent_streams,
                "native_worker_count": len(children), "completed_worker_count": len(finished_children),
                "native_collaboration_tools": [item["tool"] for item in calls],
                "model_tool_choices": [name for _identity, name in bounded.tool_calls],
                "answers": answers, "failures": failures}
        except (RuntimeError, subprocess.TimeoutExpired) as exc:
            report = {"status": "failed", "failure": type(exc).__name__, "provider_requests": bounded.requests}
    report.update(provider=profile["id"], model=profile["model"], sandbox="read-only",
        elapsed_seconds=round(time.monotonic() - started, 2), private_code_sent=False,
        worktree_isolation_tested=False, crash_recovery_tested=False)
    if report["status"] == "passed":
        report.update(cleanup_scratch(scratch, root / "build"))
    else:
        report["synthetic_failure_workspace"] = str(scratch)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report), flush=True)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
