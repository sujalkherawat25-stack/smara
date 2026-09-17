"""Run the dedicated Quick/Deep research-lane acceptance gate."""
from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smara.autonomous_agent import _get_api_key_from_vault_or_env
from smara.research_tools import WebSearchTool
from scripts.run_live_web_acceptance_v2 import run_gate


PACK = ROOT / "tests/evals/research_lanes_acceptance_v1/manifest.json"
REFS = ROOT / "tests/evals/research_lanes_acceptance_v1/references.json"
EVIDENCE = ROOT / "release/evidence/RESEARCH_LANES_ACCEPTANCE_V1.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the dedicated Quick/Deep research-lane acceptance gate")
    parser.add_argument("--smoke", action="store_true", help="run one Quick and one Deep calibration case")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--retry-failed", action="store_true", help="when resuming, rerun failed attempts and replace their records")
    parser.add_argument("--max-rupees", type=float, default=850.0)
    parser.add_argument("--max-seconds", type=float, default=10800.0)
    parser.add_argument("--max-tokens-per-attempt", type=int, default=750_000)
    parser.add_argument("--max-iterations", type=int, default=18)
    parser.add_argument("--repetitions", type=int)
    parser.add_argument("--model", default="glm5.3-flash")
    parser.add_argument("--search-provider", choices=("exa", "tavily", "brave", "serper"), default="exa")
    parser.add_argument("--pack-path", type=Path, default=PACK)
    parser.add_argument("--ref-path", type=Path, default=REFS)
    parser.add_argument("--evidence-path", type=Path, default=EVIDENCE)
    parser.add_argument("--task-ids", help="comma-separated manifest IDs for a bounded diagnostic run")
    args = parser.parse_args()

    key = os.getenv("SARVAM_API_KEY") or os.getenv("SMARA_MODEL_SARVAM_API_KEY") or _get_api_key_from_vault_or_env()
    if not key:
        key = getpass.getpass("Temporary Sarvam model API key: ").strip()
    if not key:
        raise SystemExit("A Sarvam model key is required")
    os.environ["SMARA_SEARCH_PROVIDER"] = args.search_provider
    # Keep repeated Deep turns within a predictable provider/budget envelope.
    # The research session persists the full evidence graph independently, so
    # a smaller per-request context is safe and prevents large tool histories
    # from exhausting the lane's billed-token ceiling before synthesis.
    os.environ.setdefault("SMARA_MODEL_CONTEXT_TOKENS", "65536")
    os.environ.setdefault("SMARA_CONTEXT_MAX_CHARS", "24000")
    if not (os.getenv("SMARA_SEARCH_API_KEY") or WebSearchTool._local_key(args.search_provider)):
        search_key = getpass.getpass(f"Temporary {args.search_provider} search API key: ").strip()
        if not search_key:
            raise SystemExit(f"A configured {args.search_provider} search key is required")
        os.environ["SMARA_SEARCH_API_KEY"] = search_key

    task_ids = {item.strip() for item in args.task_ids.split(",") if item.strip()} if args.task_ids else None
    if args.smoke and task_ids is None:
        task_ids = {"RL1-Q03", "RL1-D02"}
    _, code = run_gate(
        key=key,
        pack_path=args.pack_path,
        ref_path=args.ref_path,
        evidence_path=args.evidence_path,
        repetitions=args.repetitions,
        smoke=False,
        max_rupees=args.max_rupees,
        max_seconds=args.max_seconds,
        max_tokens_per_attempt=args.max_tokens_per_attempt,
        max_iterations=args.max_iterations,
        model=args.model,
        search_provider=args.search_provider,
        task_ids=task_ids,
        resume=args.resume,
        retry_failed=args.retry_failed,
    )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
