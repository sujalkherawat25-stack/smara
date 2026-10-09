"""Install the built wheel in a separate target and check its actual native entry.

Dependencies are reused from this interpreter; this is not an offline dependency
distribution test. No developer installation, personal state or provider changes.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def main():
    root = Path(__file__).resolve().parents[1]
    wheels = list((root / "build/native-wheel").glob("smara-*.whl"))
    if len(wheels) != 1:
        raise RuntimeError("Build one unambiguous candidate wheel first")
    with tempfile.TemporaryDirectory(prefix="native-wheel-smoke-", dir=root / "build") as temporary:
        directory = Path(temporary)
        target = directory / "site"
        install = subprocess.run([sys.executable, "-m", "pip", "install", "--no-deps", "--no-index", "--target", str(target), str(wheels[0])],
                                 capture_output=True, text=True, timeout=60)
        if install.returncode:
            raise RuntimeError("Isolated wheel installation failed: " + install.stderr[-2000:])
        environment = dict(os.environ, PYTHONPATH=str(target), SMARA_NATIVE_HOME=str(directory / "native-home"),
                           SMARA_NATIVE_BINARY=str(root / "native/dist/smara-native.exe"), PYTHONIOENCODING="utf-8")
        probe = subprocess.run([sys.executable, "-c", "import json, sys; import smara.native_runtime as n; print(json.dumps({'module': n.__file__, 'provenance': n.source_manifest(), 'legacy_loaded': 'smara.cli' in sys.modules or 'smara.desktop_executor' in sys.modules}))"],
                               cwd=directory, env=environment, capture_output=True, text=True, encoding="utf-8", timeout=30)
        details = json.loads(probe.stdout) if probe.returncode == 0 else {}
        help_result = subprocess.run([sys.executable, "-m", "smara.native_runtime", "--help"], cwd=directory, env=environment,
                                     capture_output=True, text=True, encoding="utf-8", timeout=30)
        frames = [{"jsonrpc": "2.0", "id": 1, "method": "initialize"},
                  {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "current_time"}}]
        reader = subprocess.run([sys.executable, "-m", "smara.native_tools", "--workspace", str(directory)], cwd=directory, env=environment,
                                input="\n".join(json.dumps(frame) for frame in frames) + "\n", capture_output=True, text=True, encoding="utf-8", timeout=30)
        replies = [json.loads(line) for line in reader.stdout.splitlines()] if reader.returncode == 0 else []
        checks = {"wheel_install_succeeded": install.returncode == 0,
                  "installed_code_not_editable_checkout": bool(details) and Path(details["module"]).resolve().is_relative_to(target.resolve()),
                  "wheel_provenance_available": details.get("provenance", {}).get("commit") == "14c8b7771ab2b617a131f5d8e55e98d18e56ed09",
                  "no_legacy_executor_imported": bool(details) and not details["legacy_loaded"],
                  "offline_native_help": help_result.returncode == 0 and "--smara-tools" in help_result.stdout,
                  "installed_mcp_clock": len(replies) == 2 and not replies[-1].get("result", {}).get("isError", True)}
        report = {"status": "passed" if all(checks.values()) else "failed", "checks": checks,
                  "dependencies_reused_from_developer_environment": True, "wheel_bundles_native_binary": False,
                  "explicit_own_runtime_used": True, "paid_provider_requests": 0, "installed_system_wide_or_published": False}
        (root / "build/native-wheel-installed-2026-10-09.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report))
        return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
