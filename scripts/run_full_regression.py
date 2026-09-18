"""Run the complete test suite and write a machine-readable release result."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "release" / "evidence" / "FULL_REGRESSION.json"


def main() -> int:
    started = time.monotonic()
    command = [sys.executable, "-m", "pytest", "-q", "--basetemp", ".pytest-tmp/full-regression"]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    stdout = (result.stdout + "\n" + result.stderr).strip()
    payload = {
        "schema": "smara.full_regression.v1",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "duration_seconds": round(time.monotonic() - started, 2),
        "command": command,
        "return_code": result.returncode,
        "passed": result.returncode == 0,
        "summary": stdout[-8000:],
    }
    content = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    payload["content_sha256"] = hashlib.sha256(content).hexdigest()
    content = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes(content)
    OUT.with_suffix(OUT.suffix + ".sha256").write_text(f"{hashlib.sha256(content).hexdigest()}  {OUT.name}\n", encoding="utf-8")
    print(json.dumps({"passed": payload["passed"], "return_code": result.returncode, "duration_seconds": payload["duration_seconds"], "evidence": str(OUT), "sha256": hashlib.sha256(content).hexdigest()}))
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())

