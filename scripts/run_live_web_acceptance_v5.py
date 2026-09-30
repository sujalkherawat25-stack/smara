"""Run the maintained 24-case canonical live-web research acceptance suite."""
from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smara.autonomous_agent import _get_api_key_from_vault_or_env
from smara.research_tools import WebSearchTool
from scripts.run_live_web_acceptance_v2 import run_gate


PACK = ROOT / "tests/evals/live_web_acceptance_v5/manifest.json"
REFS = ROOT / "tests/evals/live_web_acceptance_v5/references.json"
EVIDENCE = ROOT / "release/evidence/LIVE_WEB_ACCEPTANCE_V5.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the 24-case canonical live-web research evaluation v5")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--retry-failed", action="store_true", help="when resuming, replace failed attempts and permit a model change")
    parser.add_argument("--max-rupees", type=float, default=850.0, help="hard conservative spend ceiling")
    parser.add_argument("--max-seconds", type=float, default=10800.0, help="cumulative runtime ceiling")
    parser.add_argument("--max-tokens-per-attempt", type=int, default=150_000)
    parser.add_argument("--max-iterations", type=int, default=12)
    parser.add_argument("--repetitions", type=int, default=1, help="default is one real-model run per task; use 3 for release qualification")
    parser.add_argument("--model", default="glm5.3")
    parser.add_argument("--search-provider", choices=("exa", "tavily", "brave", "serper"), default="exa")
    parser.add_argument("--task-ids", help="comma-separated manifest IDs for a bounded diagnostic")
    parser.add_argument("--evidence-path", type=Path, default=EVIDENCE)
    parser.add_argument("--non-interactive", action="store_true", help="fail immediately when credentials are missing")
    args = parser.parse_args()

    key = os.getenv("SARVAM_API_KEY") or os.getenv("SMARA_MODEL_SARVAM_API_KEY") or _get_api_key_from_vault_or_env()
    if not key:
        if args.non_interactive or not sys.stdin.isatty():
            raise SystemExit("Configure a Sarvam model API key locally before running this evaluation")
        key = getpass.getpass("Sarvam model API key: ").strip()
    if not key:
        raise SystemExit("A Sarvam API key is required")
    if not (os.getenv("SMARA_SEARCH_API_KEY") or WebSearchTool._local_key(args.search_provider)):
        if args.non_interactive or not sys.stdin.isatty():
            raise SystemExit(f"Configure a {args.search_provider} search API key locally before running this evaluation")
        search_key = getpass.getpass(f"{args.search_provider} search API key: ").strip()
        if not search_key:
            raise SystemExit(f"A configured {args.search_provider} search key is required")
        os.environ["SMARA_SEARCH_API_KEY"] = search_key

    report, code = run_gate(
        key=key, pack_path=PACK, ref_path=REFS, evidence_path=args.evidence_path,
        repetitions=args.repetitions, smoke=False, resume=args.resume, retry_failed=args.retry_failed,
        max_rupees=args.max_rupees, max_seconds=args.max_seconds,
        max_tokens_per_attempt=args.max_tokens_per_attempt, max_iterations=args.max_iterations,
        model=args.model, search_provider=args.search_provider,
        task_ids={item.strip() for item in args.task_ids.split(",") if item.strip()} if args.task_ids else None,
    )
    print(json.dumps({"report": str(args.evidence_path), "suite": report.get("suite"),
                      "terminal_state": report.get("terminal_state"), "summary": report.get("summary")},
                     ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
