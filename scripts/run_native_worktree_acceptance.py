"""Two real-model native coding runs in copied-engine managed Git worktrees.

This proves two independent native CLI sessions, not parent-directed collab
worktree delegation. Inputs are synthetic; tests must remain byte-identical.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid
from smara.cli import _load_local_profiles, _resolve_profile_key
from smara.native_provider import ChatEndpoint, ResponsesAdapter
from smara.native_runtime import active_profile, launch_options, native_binary
from scripts.run_native_coding_acceptance import cleanup_scratch, parse_cli_output
from scripts.run_native_parallel_acceptance import ConcurrentProbeClient


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=root / "build/native-worktrees-2026-10-09.json")
    args = parser.parse_args()
    target = args.report.resolve()
    if not target.is_relative_to((root / "build").resolve()):
        raise SystemExit("Reports must stay under build")
    profiles, selected, credentials = _load_local_profiles()
    profile = active_profile(profiles, selected)
    if profile["id"] != "sarvam_glm":
        raise SystemExit("Select Sarvam GLM explicitly for this probe")
    scratch = root / "build" / ("native-coding-worktrees-" + uuid.uuid4().hex)
    workspace = scratch / "repository"
    workspace.mkdir(parents=True)
    for fixture in (root / "tests/fixtures/native_worktrees").iterdir():
        name = fixture.name.removesuffix(".txt")
        shutil.copy2(fixture, workspace / name)
    initial = {p.name: digest(p) for p in workspace.iterdir()}
    def git(*argv):
        return subprocess.run(["git", *argv], cwd=workspace, capture_output=True, text=True, encoding="utf-8", check=True).stdout
    git("init", "-b", "codex/synthetic-base")
    # Control fixture checkout bytes independently of the developer's global
    # Git autocrlf setting; tests still require exact byte identity.
    git("config", "core.autocrlf", "false")
    git("add", ".")
    git("-c", "user.name=Smara synthetic acceptance", "-c", "user.email=smara-test@example.invalid", "commit", "-m", "synthetic coding fixtures")
    report = {"status": "failed", "private_code_sent": False, "parent_collab_worktree_delegation_tested": False}
    started = time.monotonic()
    with ResponsesAdapter(ChatEndpoint(profile["base_url"], profile["model"], _resolve_profile_key(profile, credentials), profile.get("auth_header", "authorization"))) as adapter:
        bounded = ConcurrentProbeClient(adapter.client)
        adapter.client = bounded
        def run(name):
            options, env = launch_options(adapter, home=scratch / ("home-" + name), workspace=workspace)
            prompt = (f"Fix the bug in {name}.py in this disposable synthetic project. You are in a Windows PowerShell workspace. "
                      f"Inspect the implementation and run test_{name}.py before changing code; make a minimal implementation fix and rerun that test file. "
                      f"Python is available at {sys.executable}. Modify only {name}.py; do not edit tests, other implementation files, "
                      "install dependencies, make commits, ask for elevated execution, access other projects or external services. "
                      "Your managed worktree is the only writable project. Report the actual test result.")
            result = subprocess.run([str(native_binary()), *options, "--no-daemon", "exec", "--worktree", "--json", prompt],
                cwd=workspace, env=env, capture_output=True, text=True, encoding="utf-8", timeout=240,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            items, completed, failures = parse_cli_output(result)
            return {"task": name, "completed": completed and result.returncode == 0,
                    "failures": failures, "command_exits": [item.get("exit_code") for item in items if item.get("type") == "command_execution"],
                    "answers": [item["text"] for item in items if item.get("type") == "agent_message"], "stderr_tail": result.stderr[-800:] if result.returncode else ""}
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                runs = list(pool.map(run, ["pricing", "shipping"]))
            report["runs"] = runs
            paths = [Path(line[len("worktree "):]) for line in git("worktree", "list", "--porcelain").splitlines() if line.startswith("worktree ")]
            worktrees = [path for path in paths if path.resolve() != workspace.resolve()]
            if any(not path.resolve().is_relative_to(scratch.resolve()) for path in worktrees):
                raise RuntimeError("Managed worktree escaped the disposable test boundary")
            merged = scratch / "merged-verification"
            merged.mkdir()
            for name in initial:
                shutil.copy2(workspace / name, merged / name)
            chosen = {}
            for name in ["pricing", "shipping"]:
                matches = [path for path in worktrees if digest(path / (name + ".py")) != initial[name + ".py"]]
                if len(matches) != 1:
                    raise RuntimeError("Expected exactly one patched worktree for " + name)
                chosen[name] = matches[0]
                shutil.copy2(matches[0] / (name + ".py"), merged / (name + ".py"))
            verify = subprocess.run([sys.executable, "-m", "unittest", "discover", "-v"], cwd=merged, capture_output=True, text=True, encoding="utf-8")
            checks = {"two_native_managed_worktrees": len(worktrees) == 2 and chosen["pricing"] != chosen["shipping"],
                "both_native_runs_completed": all(run["completed"] and not run["failures"] for run in runs),
                "both_failures_reproduced_then_retested": all(1 in run["command_exits"] and run["command_exits"][-1] == 0 for run in runs),
                "overlapping_provider_streams": bounded.peak_concurrent_streams >= 2,
                "original_source_unchanged": all(digest(workspace / name) == expected for name, expected in initial.items()),
                "tests_unchanged_in_both_worktrees": all(digest(path / name) == initial[name] for path in worktrees for name in initial if name.startswith("test_")),
                "workers_touched_only_their_module": all(all(digest(path / (other + ".py")) == initial[other + ".py"] for other in ["pricing", "shipping"] if other != name) for name, path in chosen.items()),
                "both_fixes_verify_together": verify.returncode == 0 and "Ran 8 tests" in verify.stderr}
            report.update(status="passed" if all(checks.values()) else "failed", checks=checks, runs=runs, independent_verification=verify.stderr)
        except Exception as exc:
            report.update(failure=type(exc).__name__, details=str(exc)[:1200])
        report.update(provider_requests=bounded.requests, request_ceiling=bounded.ceiling, max_output_tokens_per_request=2048,
                      peak_concurrent_provider_streams=bounded.peak_concurrent_streams)
    report["elapsed_seconds"] = round(time.monotonic() - started, 2)
    if report["status"] == "passed":
        report.update(cleanup_scratch(scratch, root / "build"))
    else:
        report["retained_synthetic_workspace"] = str(scratch)
    target.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report), flush=True)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
