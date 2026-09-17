"""Run the no-network delegation control-plane acceptance gate."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from smara.delegation_acceptance import build_control_evidence


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="release/evidence/DELEGATION_CONTROL_ACCEPTANCE_2026-09-17.json")
    args = parser.parse_args()
    evidence = build_control_evidence()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"path": str(output), "sha256": evidence["sha256"], "gate_passed": evidence["matrix"]["gate_passed"]}, sort_keys=True))
    return 0 if evidence["matrix"]["gate_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
