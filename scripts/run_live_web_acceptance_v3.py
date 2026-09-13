"""Run the fresh held-out v3 gate or its disjoint calibration smoke."""
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


PACK = ROOT / "tests/evals/live_web_acceptance_v3/manifest.json"
REFS = ROOT / "tests/evals/live_web_acceptance_v3/references.json"
SMOKE_PACK = ROOT / "tests/evals/live_web_smoke_v3/manifest.json"
SMOKE_REFS = ROOT / "tests/evals/live_web_smoke_v3/references.json"
EVIDENCE = ROOT / "release/evidence/LIVE_WEB_ACCEPTANCE_V3.json"
SMOKE_EVIDENCE = ROOT / "release/evidence/LIVE_WEB_ACCEPTANCE_V3_SMOKE.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the held-out canonical-agent live-web acceptance v3")
    parser.add_argument("--smoke", action="store_true", help="run the disjoint two-task calibration pack")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--max-rupees", type=float, help="hard ceiling; defaults to Rs 80 for smoke or Rs 850 for full")
    parser.add_argument("--max-seconds", type=float, help="cumulative ceiling; defaults to 30 minutes for smoke or 3 hours for full")
    parser.add_argument("--max-tokens-per-attempt", type=int, default=750000)
    parser.add_argument("--model", default="glm5.3-flash")
    parser.add_argument("--search-provider", choices=("exa", "tavily", "brave", "serper"), default="exa")
    parser.add_argument(
        "--prompt-search-key",
        action="store_true",
        help="securely prompt for a process-local search key, overriding any saved provider key",
    )
    args = parser.parse_args()
    key = os.getenv("SARVAM_API_KEY") or os.getenv("SMARA_MODEL_SARVAM_API_KEY") or _get_api_key_from_vault_or_env()
    if not key:
        key = getpass.getpass("Temporary Sarvam model API key: ").strip()
    if not key:
        raise SystemExit("A Sarvam model key is required")
    if args.prompt_search_key or not (os.getenv("SMARA_SEARCH_API_KEY") or WebSearchTool._local_key(args.search_provider)):
        search_key = getpass.getpass(f"Temporary {args.search_provider} search API key: ").strip()
        if not search_key:
            raise SystemExit(f"A configured {args.search_provider} search key is required")
        os.environ["SMARA_SEARCH_API_KEY"] = search_key
    pack, refs, evidence = (SMOKE_PACK, SMOKE_REFS, SMOKE_EVIDENCE) if args.smoke else (PACK, REFS, EVIDENCE)
    max_rupees = args.max_rupees if args.max_rupees is not None else (80.0 if args.smoke else 850.0)
    max_seconds = args.max_seconds if args.max_seconds is not None else (1800.0 if args.smoke else 10800.0)
    _, code = run_gate(
        key=key, pack_path=pack, ref_path=refs, evidence_path=evidence,
        repetitions=None, smoke=False, resume=args.resume, max_rupees=max_rupees,
        max_seconds=max_seconds, max_tokens_per_attempt=args.max_tokens_per_attempt,
        model=args.model, search_provider=args.search_provider,
    )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
