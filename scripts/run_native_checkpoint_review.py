"""Revalidate an already completed model checkpoint. No model/tool replay.

Reads only a selected isolated outcome-suite session through the real native
protocol. Does not change goal completion, accept results or repair evidence.
"""
import argparse
import json
import os
from pathlib import Path
from scripts.native_session import NativeSession
from scripts.run_native_outcome_suite import saved_continuation
from smara.native_goal_runtime import checkpoint_from
from smara.native_goals import GoalStore
from smara.native_outcomes import review
from smara.native_provider import ChatEndpoint, ResponsesAdapter


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--saved", type=Path, required=True)
    parser.add_argument("--carry-budget", type=Path, required=True)
    parser.add_argument("--case", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    saved_continuation(args.saved, [args.case], args.carry_budget)
    root = args.out.resolve(); root.mkdir(parents=True, exist_ok=False)
    saved = args.saved.resolve()
    prior_env = {key: os.environ.get(key) for key in ("SMARA_NATIVE_HOME", "SMARA_DESKTOP_STATE", "SMARA_DESKTOP_CREDENTIALS")}
    report = {"mode": "local_revalidation_saved_real_model_output", "passed": False, "model_requests": 0,
              "case": args.case, "tools_replayed": 0, "goal_accepted": False, "original_reports_preserved": True}
    try:
        os.environ.update(SMARA_NATIVE_HOME=str(saved / "runtime"), SMARA_DESKTOP_STATE=str(root / "isolated/desktop.json"), SMARA_DESKTOP_CREDENTIALS=str(root / "isolated/credentials.json"))
        goals = GoalStore().list(saved / args.case)
        if len(goals) != 1 or not goals[0]["session_id"] or goals[0]["lease"]:
            raise ValueError("No unique inactive saved session")
        goal = goals[0]
        with ResponsesAdapter(ChatEndpoint("http://127.0.0.1:9/v1", "offline-no-inference", "")) as adapter:
            def unexpected_request(_message): raise RuntimeError("Unexpected authority request during read-only review")
            session = NativeSession(adapter, saved / args.case, saved / "runtime", request_handler=unexpected_request)
            try:
                session.initialize()
                session.rpc("thread/resume", {"threadId": goal["session_id"], "cwd": str(saved / args.case), "approvalPolicy": "on-request", "sandbox": goal["spec"]["sandbox"]})
                transcript = session.rpc("thread/read", {"threadId": goal["session_id"], "includeTurns": True})["thread"]
            finally: session.close()
        turn = transcript["turns"][-1]
        if turn["status"] != "completed": raise ValueError("The selected checkpoint turn was not completed")
        messages = [item["text"] for item in turn["items"] if item["type"] == "agentMessage"]
        checkpoint = checkpoint_from(messages[-1], goal["spec"]["workspace"], goal["spec"]["task_kind"])
        outcome = review(goal["spec"], checkpoint, transcript, cycle_turn_ids=[turn["id"]])
        report.update(passed=checkpoint["decision"] == "ready_for_review" and outcome["passed"],
                      decision=checkpoint["decision"], summary=checkpoint["summary"], outcome=outcome)
    except Exception as error:
        report["failure_type"] = type(error).__name__
    finally:
        for key, value in prior_env.items():
            if value is None: os.environ.pop(key, None)
            else: os.environ[key] = value
        (root / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "outcome"}))
    return 0 if report["passed"] else 1


if __name__ == "__main__": raise SystemExit(main())
