"""Scripted-provider acceptance of real native parent-directed isolated workers.

No AI/task-quality claim: only disposable committed files and actual native
spawning, sandbox execution, returned patches, and cold worker resume are tested.
Failed fixtures stay under build; no user repository changes, model billing,
automatic merges, installer changes, or publication.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid

from smara.native_provider import ChatEndpoint, ResponsesAdapter
from smara.native_runtime import native_binary
from scripts.native_session import NativeSession


def text(content):
    if isinstance(content, str):
        return content
    return "\n".join(part.get("text", "") for part in content or [] if isinstance(part, dict))


def tool(payload, suffix):
    matches = [t["function"] for t in payload.get("tools", []) if t["function"]["name"].endswith(suffix)]
    if len(matches) != 1:
        raise RuntimeError(f"Expected one native {suffix} tool")
    return matches[0]["name"]


def remaining_worker_targets(payload, children):
    """Do not wait again on an already completed first finisher."""
    observed = set()
    for message in payload["messages"]:
        if message["role"] != "tool":
            continue
        result = json.loads(message["content"])
        if isinstance(result.get("status"), dict):
            observed.update(identifier for identifier, status in result["status"].items()
                            if isinstance(status, dict) and "completed" in status)
    return [child["agent_id"] for child in children if child["agent_id"] not in observed]


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=root / "build/native-isolated-workers.json")
    args = parser.parse_args()
    target = args.report.resolve()
    if not target.is_relative_to(root / "build"):
        raise ValueError("Report must stay under build")
    scratch = root / "build" / ("native-isolated-workers-" + uuid.uuid4().hex)
    workspace = scratch / "repository"
    workspace.mkdir(parents=True)
    for name in ("alpha", "beta"):
        (workspace / f"{name}.txt").write_text(f"original-{name}\n", encoding="utf-8")
    def git(*args):
        return subprocess.run(["git", *args], cwd=workspace, check=True, capture_output=True, text=True, encoding="utf-8").stdout
    git("init", "-b", "codex/synthetic-workers")
    git("config", "core.autocrlf", "false")
    git("add", "alpha.txt", "beta.txt")
    git("-c", "user.name=Smara fixture", "-c", "user.email=fixture@example.invalid", "commit", "-m", "synthetic committed fixture")
    initial = {name: hashlib.sha256((workspace / f"{name}.txt").read_bytes()).hexdigest() for name in ("alpha", "beta")}
    # An untracked secret sentinel must not appear in the checkout or provider.
    (workspace / "private-untracked.txt").write_text("untracked-sentinel-" + uuid.uuid4().hex, encoding="utf-8")
    lock = threading.Lock()
    barrier = threading.Barrier(2)
    counts = {}
    errors, child_paths, callbacks = [], {}, []
    report = {"status": "failed", "paid_requests": 0, "private_code_sent": False,
              "scope": "offline scripted provider with actual native tools; not real-model quality", "retained_fixture": str(scratch),
              "native_sha256": hashlib.sha256(native_binary().read_bytes()).hexdigest()}
    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *_args): pass
        def do_POST(self):
            try:
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                user = next(text(message.get("content")) for message in reversed(payload["messages"]) if message["role"] == "user")
                label = "alpha" if user.startswith("OFFLINE WORKER alpha") else "beta" if user.startswith("OFFLINE WORKER beta") else "parent"
                with lock:
                    counts[label] = counts.get(label, 0) + 1
                    position = counts[label]
                    if sum(counts.values()) > 24:
                        raise RuntimeError("Offline request ceiling reached")
                calls = []
                if label != "parent" and position == 1:
                    barrier.wait(timeout=90)
                    patch = f"*** Begin Patch\n*** Update File: {label}.txt\n@@\n-original-{label}\n+fixed-{label}\n*** End Patch"
                    calls = [(tool(payload, "apply_patch"), {"input": patch})]
                elif label != "parent" and position in (2, 4):
                    # Deliberately probe outside-write denial through the actual
                    # restricted native executor, never an escalated command.
                    outside = str(workspace / f"escaped-{label}.txt").replace("'", "''")
                    command = f"$ErrorActionPreference='Stop'; try {{ Set-Content -LiteralPath '{outside}' -Value 'must-not-exist'; Write-Output 'ESCAPE_SUCCEEDED' }} catch {{ Write-Output 'OUTSIDE_WRITE_BLOCKED' }}; Get-Content -LiteralPath './{label}.txt'; (Get-Location).Path"
                    calls = [(tool(payload, "exec_command"), {"cmd": command, "max_output_tokens": 1000})]
                elif label == "parent" and position == 1:
                    calls = [(tool(payload, "spawn_agent"), {"message": f"OFFLINE WORKER {name}: synthetic fixture only; no nested workers", "worktree": True, "fork_context": False}) for name in ("alpha", "beta")]
                elif label == "parent" and position == 2:
                    results = [json.loads(message["content"]) for message in payload["messages"] if message["role"] == "tool"]
                    for name, result in zip(("alpha", "beta"), results):
                        if not result.get("worktree") or not result.get("agent_id"):
                            raise RuntimeError("Isolated spawn did not return a checkout and native child")
                        child_paths[name] = result
                    calls = [(tool(payload, "wait_agent"), {"targets": [result["agent_id"] for result in results], "timeout_ms": 60000})]
                elif label == "parent" and position == 3:
                    # First-completion wait need not deliver both workers.
                    remaining = remaining_worker_targets(payload, child_paths.values())
                    calls = [(tool(payload, "wait_agent"), {"targets": remaining or [result["agent_id"] for result in child_paths.values()], "timeout_ms": 60000})]
                elif label == "parent" and position == 5:
                    calls = [(tool(payload, "resume_agent"), {"id": result["agent_id"]}) for result in child_paths.values()]
                elif label == "parent" and position == 6:
                    calls = [(tool(payload, "send_input"), {"target": result["agent_id"], "message": f"OFFLINE WORKER {name}: cold resume; verify existing checkout only, no patch replay"}) for name, result in child_paths.items()]
                elif label == "parent" and position == 7:
                    calls = [(tool(payload, "wait_agent"), {"targets": [result["agent_id"] for result in child_paths.values()], "timeout_ms": 60000})]
                elif label == "parent" and position == 8:
                    # Only this new turn's wait result counts; earlier worker
                    # completions belong to the pre-restart turn.
                    latest_wait = next(message for message in reversed(payload["messages"]) if message["role"] == "tool")
                    remaining = remaining_worker_targets({"messages": [latest_wait]}, child_paths.values())
                    calls = [(tool(payload, "wait_agent"), {"targets": remaining or [result["agent_id"] for result in child_paths.values()], "timeout_ms": 60000})]
                else:
                    results = [text(message["content"]) for message in payload["messages"] if message["role"] == "tool"]
                    if label != "parent" and position in (3, 5) and not any("OUTSIDE_WRITE_BLOCKED" in result and f"fixed-{label}" in result for result in results):
                        raise RuntimeError("Actual worker write/denial evidence missing")
                delta = {"tool_calls": [{"index": index, "id": f"fixture-{label}-{position}-{index}", "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}} for index, (name, arguments) in enumerate(calls)]} if calls else {"content": f"OFFLINE {label} native fixture finished; not AI quality evidence"}
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(("data: " + json.dumps({"choices": [{"index": 0, "delta": delta, "finish_reason": "tool_calls" if calls else "stop"}]}) + "\n\ndata: [DONE]\n\n").encode())
                self.wfile.flush()
            except Exception as exc:
                with lock: errors.append(type(exc).__name__ + ": " + str(exc))
                self.send_error(500)
    provider = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    provider.daemon_threads = True
    threading.Thread(target=provider.serve_forever, daemon=True).start()
    def decide(message):
        callbacks.append(message["method"])
        # An in-checkout patch should need no widening. Any unexpected request
        # is denied, including one with a fixture-like model-controlled ID.
        if message["method"] in {"item/fileChange/requestApproval", "item/commandExecution/requestApproval"}:
            return {"decision": "decline"}
        raise RuntimeError("Unexpected fixture permission request")
    session = None
    started = time.monotonic()
    try:
        with ResponsesAdapter(ChatEndpoint(f"http://127.0.0.1:{provider.server_port}/v1", "offline-workers", "")) as adapter:
            session = NativeSession(adapter, workspace, scratch / "home", workers_enabled=True, request_handler=decide, timeout=180)
            session.initialize()
            thread = session.rpc("thread/start", {"cwd": str(workspace), "approvalPolicy": "on-request", "sandbox": "workspace-write"})["thread"]["id"]
            session.rpc("turn/start", {"threadId": thread, "input": [{"type": "text", "text": "OFFLINE PARENT: run two synthetic isolated workers", "text_elements": []}]})
            completed = session.wait(lambda message: message.get("method") == "turn/completed" and message["params"].get("threadId") == thread)
            history = session.rpc("thread/read", {"threadId": thread, "includeTurns": True})
            children = list(child_paths.values())
            worker_read = [session.rpc("thread/read", {"threadId": child["agent_id"], "includeTurns": True}) for child in children]
            session.close()
            session = NativeSession(adapter, workspace, scratch / "home", workers_enabled=True, request_handler=decide)
            session.initialize()
            before_resume = sum(counts.values())
            restored = session.rpc("thread/resume", {"threadId": thread, "cwd": str(workspace), "approvalPolicy": "on-request", "sandbox": "workspace-write"})
            restored_children = [session.rpc("thread/read", {"threadId": child["agent_id"], "includeTurns": True}) for child in children]
            no_replay = sum(counts.values()) == before_resume
            before = len(session.events)
            session.rpc("turn/start", {"threadId": thread, "input": [{"type": "text", "text": "OFFLINE PARENT: explicitly resume the existing children and verify their original checkouts", "text_elements": []}]})
            cold_completed = session.wait(lambda message: message.get("method") == "turn/completed" and message["params"].get("threadId") == thread, since=before)
            cold_children = [session.rpc("thread/read", {"threadId": child["agent_id"], "includeTurns": True}) for child in children]
            def same_path(actual, expected):
                return isinstance(actual, str) and Path(actual).resolve() == Path(expected).resolve()
            checks = {
                "native_parent_completed": completed["params"]["turn"]["status"] == "completed",
                "two_native_children": len(children) == 2 and len({child["agent_id"] for child in children}) == 2,
                "distinct_bounded_checkouts": len(children) == 2 and children[0]["worktree"] != children[1]["worktree"] and all(Path(child["worktree"]).resolve().is_relative_to((workspace / ".smara/worker-worktrees").resolve()) for child in children),
                "parent_code_unchanged": all(hashlib.sha256((workspace / f"{name}.txt").read_bytes()).hexdigest() == initial[name] for name in initial),
                "only_assigned_modules_changed": all((Path(child_paths[name]["worktree"]) / f"{name}.txt").read_text().strip() == f"fixed-{name}" and (Path(child_paths[name]["worktree"]) / f"{'beta' if name == 'alpha' else 'alpha'}.txt").read_text().startswith("original-") for name in initial),
                "parent_write_probes_denied": not any(workspace.glob("escaped-*.txt")),
                "untracked_files_not_copied": all(not (Path(child["worktree"]) / "private-untracked.txt").exists() for child in children),
                "worker_cwd_persisted": all(same_path(result["thread"]["cwd"], child["worktree"]) for child, result in zip(children, worker_read)),
                "cold_parent_and_children_restored": restored["thread"]["id"] == thread and all(same_path(result["thread"]["cwd"], child["worktree"]) for child, result in zip(children, restored_children)),
                "explicit_cold_workers_executed_in_original_checkouts": cold_completed["params"]["turn"]["status"] == "completed" and counts.get("alpha") == counts.get("beta") == 5 and all(same_path(result["thread"]["cwd"], child["worktree"]) for child, result in zip(children, cold_children)),
                "both_worker_turns_completed": len(cold_children) == 2 and all(len(result["thread"]["turns"]) == 2 and all(turn["status"] == "completed" for turn in result["thread"]["turns"]) for result in cold_children),
                "resume_did_not_replay_inference": no_replay,
                "no_provider_errors": not errors,
            }
            report.update(status="passed" if all(checks.values()) else "failed", checks=checks, child_ids=[child["agent_id"] for child in children], parent_id=thread, history_turn_count=len(history["thread"]["turns"]),
                          worker_locations=[{"expected": child["worktree"], "initial": initial["thread"]["cwd"], "cold": cold["thread"]["cwd"]} for child, initial, cold in zip(children, worker_read, cold_children)])
    except Exception as exc:
        report.update(error=type(exc).__name__, detail=str(exc)[:1500])
    finally:
        if session: session.close()
        provider.shutdown()
        provider.server_close()
    report.update(elapsed_seconds=round(time.monotonic() - started, 2), provider_counts=counts, provider_errors=errors, native_approval_callbacks=callbacks)
    target.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report), flush=True)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
