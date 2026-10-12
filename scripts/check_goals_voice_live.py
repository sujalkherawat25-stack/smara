"""Synthetic-only gate: 12 requests / INR300 plus one TTS/STT pair initially;
the separately approved coding/mixed rerun permits 8 requests / INR250, no speech.

No private repository code, real history, Telegram messages, remote memory,
automatic approvals, or retries. Reservations are persisted before requests.
The output directory must be new; reruns require explicit new authorization.
"""
from contextlib import contextmanager
import argparse
import base64
import json
import os
from pathlib import Path
import subprocess
import threading
import time

from smara.native_goal_runtime import execute_cycle
from smara.native_goals import GoalStore
from smara.native_profiles import load_profiles, resolve_profile_key
from smara.native_provider import ChatEndpoint
from smara.native_runtime import active_profile
from smara.native_voice import operate as voice


class LiveGuard:
    def __init__(self, path, *, requests=12, rupees=300):
        self.path, self.lock = path, threading.Lock()
        if (requests, rupees) not in {(8, 250), (12, 300)}:
            raise ValueError("Unsupported authorization ceiling")
        self.ledger = {"requests": 0, "request_ceiling": requests, "reserved_inr_estimate": 0., "ceiling_inr": rupees, "actual_billing_known": False}
        self.save()

    def save(self):
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.ledger, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    def wrap(self, client):
        guard = self
        class Client:
            @contextmanager
            def stream(self, *args, **kwargs):
                payload = dict(kwargs["json"])
                payload["max_tokens"] = min(payload.get("max_tokens", 2048), 2048)
                cost = (len(json.dumps(payload, ensure_ascii=False).encode()) + 1024 + payload["max_tokens"]) * 396 / 1_000_000
                with guard.lock:
                    if guard.ledger["requests"] >= guard.ledger["request_ceiling"] or guard.ledger["reserved_inr_estimate"] + cost > guard.ledger["ceiling_inr"]:
                        raise RuntimeError("Approved cumulative live ceiling reached")
                    guard.ledger["requests"] += 1
                    guard.ledger["reserved_inr_estimate"] += cost
                    guard.save()
                with client.stream(*args, **{**kwargs, "json": payload}) as response:
                    yield response
            def close(self):
                client.close()
        return Client()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--rerun-coding-mixed", action="store_true", help="Separate user-approved 8-request/INR250 rerun, no speech or research-only calls")
    args = parser.parse_args()
    root = args.out.resolve()
    root.mkdir(parents=True, exist_ok=False)
    report = {"synthetic_only": True, "speech": {}, "goals": [], "limitations": ["Citation provenance is not semantic entailment", "These are narrow synthetic tasks, not broad capability parity", "No live Telegram or microphone check"]}
    guard = LiveGuard(root / "chat-reservations.json", requests=8 if args.rerun_coding_mixed else 12, rupees=250 if args.rerun_coding_mixed else 300)
    original = {name: os.environ.get(name) for name in ("SMARA_NATIVE_HOME", "SMARA_DESKTOP_STATE", "SMARA_DESKTOP_CREDENTIALS")}
    try:
        profiles, selected, credentials = load_profiles()
        profile = active_profile(profiles, selected)
        endpoint = ChatEndpoint(profile["base_url"], profile["model"], resolve_profile_key(profile, credentials, allow_environment=False), profile.get("auth_header", "authorization"), profile.get("context_window"))
        if not endpoint.api_key or endpoint.base_url.rstrip("/") != "https://api.sarvam.ai/v2":
            raise ValueError("This authorization is for the configured Sarvam endpoint only")
        report["model"] = endpoint.model
        sentence = "Smara can help me check tests and review a research report."
        # Each speech reservation is written before outbound work, including failures.
        report["speech"] = {"skipped": True, "detail": "Prior approved speech check is retained; no repeat"} if args.rerun_coding_mixed else {"tts_attempted": True, "stt_attempted": False, "speech_billing_known": False}
        (root / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        try:
            if not args.rerun_coding_mixed:
                audio = voice({"action": "synthesize", "text": sentence, "language": "en-IN"})
                (root / "synthetic.wav").write_bytes(base64.b64decode(audio["audio"]))
                report["speech"].update(tts_passed=True, stt_attempted=True)
                (root / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
                transcript = voice({"action": "transcribe", "audio": audio["audio"], "mime": "audio/wav", "language": "en-IN"})
                text = transcript["transcript"]
                report["speech"].update(transcript=text, stt_passed=all(word in text.lower() for word in ("tests", "research", "report")))
        except Exception as exc:
            report["speech"]["failure_type"] = type(exc).__name__
        home = root / "native-home"
        home.mkdir()
        state = root / "isolated-state.json"
        state.write_text("{}", encoding="utf-8")
        vault = root / "isolated-vault.json"
        vault.write_text("{}", encoding="utf-8")
        os.environ.update(SMARA_NATIVE_HOME=str(home), SMARA_DESKTOP_STATE=str(state), SMARA_DESKTOP_CREDENTIALS=str(vault))
        store = GoalStore(home / "native-goals.sqlite")
        for kind in (("coding", "mixed") if args.rerun_coding_mixed else ("coding", "research", "mixed")):
            workspace = root / kind
            workspace.mkdir()
            if kind != "research":
                (workspace / "calculator.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
                (workspace / "test_calculator.py").write_text("from calculator import add\n\ndef test_add():\n    assert add(2, 3) == 5\n    assert add(-2, 3) == 1\n", encoding="utf-8")
                original_tests = (workspace / "test_calculator.py").read_bytes()
            code_task = f'Only calculator.py and test_calculator.py are present. Fix calculator.py so both tests pass; do not change test_calculator.py. Run "{os.sys.executable}" -m pytest -p no:cacheprovider on this workspace (no compound command). Include calculator.py in outputs. '
            research_task = "Read https://docs.python.org/3/library/json.html and https://docs.python.org/3/tutorial/inputoutput.html with fetch_url. Explain briefly how json.loads differs from json.load. Include two factual claims with exact quotes copied from the fetched text and source URLs. "
            objective = code_task if kind == "coding" else research_task if kind == "research" else code_task + research_task
            identity = store.action({"action": "create", "workspace": str(workspace), "profile_id": profile["id"], "task_kind": kind,
                "objective": objective + "Do not explore other folders or inspect credentials. Batch independent reads; avoid redundant inventory. Complete within four model requests including the final structured checkpoint for user review.",
                "success_criteria": "Both synthetic tests pass" if kind == "coding" else "Explain both JSON functions using observed source excerpts" if kind == "research" else "Tests pass and both JSON functions are supported by observed source excerpts",
                "sandbox": "read-only" if kind == "research" else "workspace-write", "max_cycles": 1, "max_requests": 4, "max_token_estimate": 1_000_000,
                "cycle_seconds": 180, "interval_seconds": 0})
            goal = store.claim()
            started = time.monotonic()
            # Approval waits in production may be indefinite; an acceptance run
            # must still have a wall-clock bound without approving the request.
            expiry = threading.Timer(180, lambda: store.action({"action": "pause", "id": identity}))
            expiry.daemon = True; expiry.start()
            try:
                result = execute_cycle(store, goal, endpoint=endpoint, provider_guard=guard.wrap)
            finally:
                expiry.cancel()
            snapshot = {"kind": kind, "state": result["state"], "detail": result["detail"], "requests": result["requests"], "session_id": result["session_id"], "elapsed_seconds": round(time.monotonic() - started, 1), "checkpoint": result["checkpoint"]}
            if kind != "research":
                checked = subprocess.run([os.sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(workspace)], cwd=workspace, capture_output=True, text=True, timeout=30)
                snapshot["independent_tests_passed"] = checked.returncode == 0
                snapshot["fixture_tests_unchanged"] = (workspace / "test_calculator.py").read_bytes() == original_tests
            report["goals"].append(snapshot)
            (root / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(json.dumps({k: v for k, v in snapshot.items() if k != "checkpoint"}), flush=True)
    except Exception as exc:
        report["failure_type"] = type(exc).__name__
    finally:
        for name, value in original.items():
            if value is None: os.environ.pop(name, None)
            else: os.environ[name] = value
        report["chat_budget"] = guard.ledger
        report["passed"] = (args.rerun_coding_mixed or report["speech"].get("stt_passed", False)) and len(report["goals"]) == (2 if args.rerun_coding_mixed else 3) and all(g["state"] == "ready_for_review" and g.get("independent_tests_passed", True) and g.get("fixture_tests_unchanged", True) for g in report["goals"])
        (root / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "speech": report["speech"], "budget": report["chat_budget"], "report": str(root / "report.json"), "failure_type": report.get("failure_type")}), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
