"""Bounded real-model native MCP + compaction probe, public/synthetic data only."""
import argparse
import json
from pathlib import Path
import time
import uuid
from smara.cli import _load_local_profiles, _resolve_profile_key
from smara.native_runtime import active_profile
from smara.native_provider import ChatEndpoint, ResponsesAdapter
from scripts.native_session import NativeSession
from scripts.run_native_coding_acceptance import BoundedCodingClient, cleanup_scratch


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=root / "build/native-readers-2026-10-09.json")
    args = parser.parse_args()
    target = args.report.resolve()
    if not target.is_relative_to((root / "build").resolve()):
        raise SystemExit("Reports must stay under build")
    profiles, selected, credentials = _load_local_profiles()
    profile = active_profile(profiles, selected)
    if profile["id"] != "sarvam_glm":
        raise SystemExit("Select Sarvam GLM explicitly for this probe")
    scratch = root / "build" / ("native-coding-readers-" + uuid.uuid4().hex)
    workspace = scratch / "workspace"
    workspace.mkdir(parents=True)
    memory = workspace / ".smara/native-memory.md"
    memory.parent.mkdir()
    memory.write_text("Synthetic project name: Kestrel. Its rollout requires a rollback plan.\n", encoding="utf-8")
    prompt = ("This is a synthetic/public native reader test. Use the Smara reader tools to read the actual current time, "
              "read the local memory note, and fetch https://www.rfc-editor.org/rfc/rfc9110.txt . "
              "Briefly report the local-memory project name and rollout requirement, the UTC date returned by the clock, "
              "and the RFC title with its source URL. Search snippets are not evidence. Do not use shell tools, change files, "
              "contact other services, or delegate workers. Keep your answer short.")
    report = {"status": "failed", "private_code_sent": False, "request_ceiling": 12, "max_output_tokens_per_request": 2048}
    started = time.monotonic()
    session = None
    with ResponsesAdapter(ChatEndpoint(profile["base_url"], profile["model"], _resolve_profile_key(profile, credentials), profile.get("auth_header", "authorization"))) as adapter:
        bounded = BoundedCodingClient(adapter.client, ceiling=12)
        adapter.client = bounded
        try:
            session = NativeSession(adapter, workspace, scratch / "home", tools_enabled=True, timeout=150)
            session.initialize()
            thread = session.rpc("thread/start", {"cwd": str(workspace), "approvalPolicy": "on-request", "sandbox": "workspace-write"})["thread"]["id"]
            session.rpc("turn/start", {"threadId": thread, "input": [{"type": "text", "text": prompt, "text_elements": []}]})
            terminal = session.wait(lambda event: event.get("method") == "turn/completed")
            items = [event["params"]["item"] for event in session.events if event.get("method") == "item/completed"]
            tools = [item for item in items if item.get("type") == "mcpToolCall"]
            answers = [item["text"] for item in items if item.get("type") == "agentMessage"]
            successful = {item["tool"] for item in tools if item.get("status") == "completed" and not item.get("error")}
            report["checks"] = {"native_turn_completed": terminal["params"]["turn"]["status"] == "completed",
                "actual_mcp_clock_memory_fetch": {"current_time", "memory_read", "fetch_url"}.issubset(successful),
                "answer_uses_memory_and_source": any("Kestrel" in text and "rollback" in text.lower() and "rfc9110" in text.lower() and "http semantics" in text.lower() for text in answers),
                "memory_unchanged": memory.read_text(encoding="utf-8") == "Synthetic project name: Kestrel. Its rollout requires a rollback plan.\n"}
            before = len(session.events)
            session.rpc("thread/compact/start", {"threadId": thread})
            compacted = session.wait(lambda event: event.get("method") == "turn/completed", since=before)
            compaction_items = [event.get("params", {}).get("item", {}) for event in session.events[before:] if event.get("method") == "item/completed"]
            report["checks"]["native_compaction_completed"] = compacted["params"]["turn"]["status"] == "completed" and any(item.get("type") == "contextCompaction" for item in compaction_items)
            continued_begin = len(session.events)
            session.rpc("turn/start", {"threadId": thread, "input": [{"type": "text", "text": "Without using any tools, what was the synthetic project name and required rollout safeguard?", "text_elements": []}]})
            continued = session.wait(lambda event: event.get("method") == "turn/completed", since=continued_begin)
            final = [event["params"]["item"].get("text", "") for event in session.events[before:] if event.get("method") == "item/completed" and event["params"]["item"].get("type") == "agentMessage"]
            report["checks"]["recall_after_compaction"] = continued["params"]["turn"]["status"] == "completed" and any("Kestrel" in text and "rollback" in text.lower() for text in final)
            report.update(status="passed" if all(report["checks"].values()) else "failed", mcp_tools=[item["tool"] for item in tools],
                          mcp_errors=[item.get("error") for item in tools if item.get("error")], answers=answers, after_compaction_answers=final)
        except Exception as exc:
            report.update(failure=type(exc).__name__, details=str(exc)[:1500])
        finally:
            if session:
                session.close()
            report["provider_requests"] = bounded.requests
    report.update(elapsed_seconds=round(time.monotonic() - started, 2), provider=profile["id"], model=profile["model"])
    if report["status"] == "passed":
        report.update(cleanup_scratch(scratch, root / "build"))
    else:
        report["retained_synthetic_workspace"] = str(scratch)
    target.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report), flush=True)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
