"""Run the deterministic integration acceptance gate and seal its evidence."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_DIR = ROOT / "release" / "evidence"
TESTS = [
    "tests/test_integration_acceptance_gate.py",
    "tests/test_production_skills_and_plugins.py",
    "tests/test_skill_lifecycle.py",
    "tests/test_mcp_client.py",
    "tests/test_plugins.py",
]


def main() -> int:
    started = datetime.now(timezone.utc)
    command = [sys.executable, "-m", "pytest", *TESTS, "-q"]
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    output = (completed.stdout + "\n" + completed.stderr).strip()
    evidence = {
        "schema": "smara.integration_acceptance.v1",
        "run_id": f"integration_{uuid.uuid4().hex}",
        "started_at": started.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "command": command,
        "tests": TESTS,
        "return_code": completed.returncode,
        "passed": completed.returncode == 0,
        "summary": output[-4000:],
        "boundaries": {
            "remote_mcp_discovery_and_call": True,
            "pkce_oauth_exchange": True,
            "credential_expiry_refresh": True,
            "connector_health_recovery": True,
            "plugin_lifecycle": True,
            "skill_promotion_reuse_rollback": True,
        },
    }
    payload = (json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    evidence["content_sha256"] = hashlib.sha256(payload).hexdigest()
    encoded = (json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    path = EVIDENCE_DIR / "INTEGRATION_ACCEPTANCE.json"
    path.write_bytes(encoded)
    path.with_suffix(path.suffix + ".sha256").write_text(f"{hashlib.sha256(encoded).hexdigest()}  {path.name}\n", encoding="utf-8")
    print(json.dumps({"passed": evidence["passed"], "return_code": completed.returncode, "evidence": str(path), "sha256": hashlib.sha256(encoded).hexdigest()}))
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
