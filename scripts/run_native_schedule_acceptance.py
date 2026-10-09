"""One real native scheduled turn on synthetic text; no recurring job enabled."""
import json
from pathlib import Path
import uuid
from smara.native_profiles import load_profiles
from smara.native_schedule import ScheduleStore, supervisor_lock, tick
from scripts.run_native_coding_acceptance import cleanup_scratch


def main():
    root = Path(__file__).resolve().parents[1]
    _profiles, selected, _credentials = load_profiles()
    if selected != "sarvam_glm":
        raise SystemExit("Select Sarvam GLM explicitly for this probe")
    scratch = root / "build" / ("native-coding-schedule-" + uuid.uuid4().hex)
    scratch.mkdir()
    store = ScheduleStore(scratch / "schedule.sqlite")
    identity = store.add("synthetic one-turn gate", {"workspace": str(scratch), "profile_id": selected,
        "prompt": "This is a synthetic read-only test. What is 17 times 23? Reply with the result only. Do not use any tools.",
        "interval_seconds": 3600, "max_requests": 2, "public_readers": False})
    paused_initially = store.list()[0]["enabled"] == 0
    store.enable(identity, True)
    with supervisor_lock(store.path):
        store.recover_unknown()
        result = tick(store)
    store.enable(identity, False)
    trace = Path(result["trace"]).read_text(encoding="utf-8") if result.get("trace") else ""
    events = [json.loads(line) for line in trace.splitlines() if line.startswith("{")]
    answers = [event["item"].get("text", "") for event in events if event.get("type") == "item.completed" and event["item"].get("type") == "agent_message"]
    checks = {"paused_until_enabled": paused_initially, "real_native_turn_completed": result["status"] == "completed",
        "answer_correct": any(answer.strip() == "391" for answer in answers), "read_only_no_interactive_grants": result.get("sandbox") == "read-only" and result.get("approval_policy") == "never",
        "within_request_budget": 1 <= result.get("provider_requests", 0) <= 2, "disabled_after_test": store.list()[0]["enabled"] == 0}
    report = {"status": "passed" if all(checks.values()) else "failed", "checks": checks, "result": result, "private_code_sent": False, "recurring_job_left_enabled": False}
    if report["status"] == "passed":
        report.update(cleanup_scratch(scratch, root / "build"))
    (root / "build/native-schedule-2026-10-09.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report), flush=True)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
