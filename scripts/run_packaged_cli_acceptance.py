"""Acceptance of the actual frozen CLI; optional public GET, never paid calls."""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-fetch", action="store_true", help="Also fetch public RFC 9110 using the frozen reader")
    parser.add_argument("--binary", type=Path, help="Test an installed portable CLI, not only the build candidate")
    parser.add_argument("--report", type=Path, help="Keep separate evidence for candidate and installed checks")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    binary = (args.binary or root / "build/native-cli-candidate/smara.exe").resolve()
    checks = {}
    result = subprocess.run([str(binary), "source-status"], capture_output=True, text=True, encoding="utf-8", timeout=30)
    manifest = json.loads(result.stdout)
    checks["bundled_own_runtime_resolved"] = result.returncode == 0 and manifest["built"] and Path(manifest["binary"]).resolve() == binary.parent / "native/smara-native.exe"
    checks["fork_provenance_current"] = "explicit provider context metadata" in " ".join(manifest["local_modifications"])
    result = subprocess.run([str(binary), "--help"], capture_output=True, text=True, encoding="utf-8", timeout=30)
    checks["offline_integration_help"] = result.returncode == 0 and all(command in result.stdout for command in ("--smara-tools", "schedule --help", "tools-serve", "source-status"))
    with tempfile.TemporaryDirectory(prefix="native-cli-offline-", dir=root / "build") as temporary:
        workspace = Path(temporary)
        memory = workspace / ".smara/native-memory.md"
        memory.parent.mkdir()
        memory.write_text("स्मारा 😀 synthetic memory", encoding="utf-8")
        requests = [{"jsonrpc": "2.0", "id": 1, "method": "initialize"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "current_time"}},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "memory_read"}}]
        result = subprocess.run([str(binary), "--native-tools", "--workspace", str(workspace)], input="\n".join(json.dumps(r) for r in requests) + "\n",
            capture_output=True, text=True, encoding="utf-8", timeout=30)
        replies = [json.loads(line) for line in result.stdout.splitlines()]
        checks["frozen_stdio_readers_work"] = result.returncode == 0 and len(replies) == 3
        checks["unicode_memory_preserved"] = json.loads(replies[-1]["result"]["content"][0]["text"])["text"] == memory.read_text(encoding="utf-8")
        if args.live_fetch:
            request = {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "fetch_url", "arguments": {"url": "https://www.rfc-editor.org/rfc/rfc9110.txt"}}}
            frames = [{"jsonrpc": "2.0", "id": 1, "method": "initialize"}, request]
            result = subprocess.run([str(binary), "--native-tools", "--workspace", str(workspace)], input="\n".join(json.dumps(frame) for frame in frames) + "\n",
                capture_output=True, text=True, encoding="utf-8", timeout=45)
            reply = json.loads(result.stdout.splitlines()[-1])
            succeeded = result.returncode == 0 and "result" in reply and not reply["result"].get("isError")
            fetched = json.loads(reply["result"]["content"][0]["text"]) if succeeded else {}
            checks["frozen_public_fetch_dependency_path"] = succeeded and "HTTP Semantics" in fetched.get("text", "") and fetched.get("total_chars", 0) > 8000 and fetched.get("next_offset") == 8000
        result = subprocess.run([str(binary), "schedule", "--store", str(workspace / "scheduler.sqlite"), "list"], capture_output=True, text=True, encoding="utf-8", timeout=30)
        checks["frozen_schedule_cli_loads"] = result.returncode == 0 and json.loads(result.stdout) == []
    result = subprocess.run([str(binary), "legacy", "--help"], capture_output=True, text=True, encoding="utf-8", timeout=30)
    checks["legacy_loop_not_bundled_or_entered"] = result.returncode == 1 and "does not bundle the legacy engine" in result.stderr
    report = {"status": "passed" if all(checks.values()) else "failed", "binary": str(binary), "checks": checks, "paid_provider_requests": 0, "public_network_fetch": args.live_fetch, "installed_or_published": False}
    report_name = "native-packaged-cli-live-2026-10-09.json" if args.live_fetch else "native-packaged-cli-2026-10-09.json"
    target = (args.report or root / "build" / report_name).resolve()
    if not target.is_relative_to((root / "build").resolve()):
        raise ValueError("Keep acceptance reports inside build")
    target.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
