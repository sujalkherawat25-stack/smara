"""Run the release checks and seal one auditable release-gate record."""
from __future__ import annotations

import base64
import hashlib
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "release" / "evidence"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run(command: list[str], cwd: Path) -> dict[str, object]:
    proc = subprocess.run(command, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return {
        "command": command,
        "cwd": str(cwd),
        "return_code": proc.returncode,
        "passed": proc.returncode == 0,
        "stdout_tail": proc.stdout[-4000:],
        "stderr_tail": proc.stderr[-4000:],
    }


def _verify_signature(artifact: Path, record_path: Path) -> dict[str, object]:
    record = json.loads(record_path.read_text(encoding="utf-8"))
    content = artifact.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    key = base64.urlsafe_b64decode(record["public_key"] + "=" * (-len(record["public_key"]) % 4))
    sig = base64.urlsafe_b64decode(record["signature"] + "=" * (-len(record["signature"]) % 4))
    Ed25519PublicKey.from_public_bytes(key).verify(sig, content)
    return {"verified": True, "sha256": digest, "signature": str(record_path)}


def main() -> int:
    version = (ROOT / "release" / "VERSION").read_text(encoding="utf-8").strip()
    wheel = ROOT / "release" / f"smara-{version}-py3-none-any.whl"
    wheel_sig = wheel.with_suffix(wheel.suffix + ".sig.json")
    installed = Path.home() / "AppData" / "Local" / "Smara Desktop"
    built_executor = ROOT / "build" / f"desktop-executor-{version}" / "smara-desktop.exe"
    installed_executor = installed / "resources" / "smara-desktop.exe"
    installed_shell = installed / "smara-desktop.exe"

    integration = json.loads((EVIDENCE / "INTEGRATION_ACCEPTANCE.json").read_text(encoding="utf-8"))
    regression = json.loads((EVIDENCE / "FULL_REGRESSION.json").read_text(encoding="utf-8"))
    checks: list[dict[str, object]] = []
    npm = shutil.which("npm.cmd") or shutil.which("npm") or "npm.cmd"
    cargo = shutil.which("cargo.exe") or shutil.which("cargo") or "cargo.exe"
    checks.append(_run([npm, "run", "build"], ROOT / "apps" / "desktop"))
    checks.append(_run([cargo, "check"], ROOT / "apps" / "desktop" / "src-tauri"))
    cli = ROOT / ".venv" / "Scripts" / "smara.exe"
    checks.append(_run([str(cli), "--version"], ROOT))
    checks.append(_run([str(cli), "--workspace", str(ROOT), "skills", "list"], ROOT))
    checks.append(_run([str(cli), "skills", "--help"], ROOT))
    checks.append(_run([str(cli), "plugins", "--help"], ROOT))
    executor = _run([str(installed_executor), "--skills"], ROOT)
    if executor["return_code"] == 0:
        raw_executor = subprocess.run([str(installed_executor), "--skills"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
        executor["skill_count"] = raw_executor.stdout.count('"capability"')
    checks.append(executor)

    versions = {
        "release": version,
        "wheel": version,
        "desktop_package": json.loads((ROOT / "apps" / "desktop" / "package.json").read_text(encoding="utf-8"))["version"],
        "tauri_cargo": re.search(r'^version\s*=\s*"([^"]+)"', (ROOT / "apps" / "desktop" / "src-tauri" / "Cargo.toml").read_text(encoding="utf-8"), re.MULTILINE).group(1),
    }
    artifact_hashes = {"wheel": _sha(wheel), "built_executor": _sha(built_executor), "installed_executor": _sha(installed_executor), "installed_shell": _sha(installed_shell)}
    record = {
        "schema": "smara.release_gate.v1",
        "version": version,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "integration_acceptance": {"passed": integration.get("passed", False), "evidence": str(EVIDENCE / "INTEGRATION_ACCEPTANCE.json")},
        "full_regression": {"passed": regression.get("passed", False), "summary": regression.get("summary", ""), "evidence": str(EVIDENCE / "FULL_REGRESSION.json")},
        "version_consistency": {"passed": len(set(versions.values())) == 1, "values": versions},
        "wheel_signature": _verify_signature(wheel, wheel_sig),
        "artifact_hashes": artifact_hashes,
        "checks": checks,
    }
    record["passed"] = bool(record["integration_acceptance"]["passed"] and record["full_regression"]["passed"] and record["version_consistency"]["passed"] and record["wheel_signature"]["verified"] and all(c["passed"] for c in checks))
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    out = EVIDENCE / f"RELEASE_GATE_{version}.json"
    out.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    digest = _sha(out)
    out.with_suffix(out.suffix + ".sha256").write_text(digest + "  " + out.name + "\n", encoding="utf-8")
    print(json.dumps({"passed": record["passed"], "evidence": str(out), "sha256": digest}))
    return 0 if record["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
