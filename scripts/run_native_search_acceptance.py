"""One opt-in public search through frozen MCP. No model/private-code requests."""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile
from urllib.parse import urlsplit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executor", type=Path, required=True)
    parser.add_argument("--allow-live-search", action="store_true")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if not args.allow_live_search:
        parser.error("Explicit --allow-live-search required: one search-provider request")
    report_path = args.report.resolve()
    if not report_path.is_relative_to((root / "build").resolve()):
        parser.error("Keep private acceptance reports in build")
    if not args.executor.is_file():
        parser.error("Frozen executor not found")
    with tempfile.TemporaryDirectory(prefix="native-search-", dir=root / "build") as scratch:
        requests = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                    {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "web_search", "arguments": {"query": "Python heapq official documentation", "max_results": 3}}}]
        response = subprocess.run([str(args.executor.resolve()), "--native-tools", "--workspace", scratch],
            input="\n".join(json.dumps(r) for r in requests) + "\n", capture_output=True, text=True, encoding="utf-8", timeout=50)
        # Never print stderr or raw provider payloads from a credentialed run.
        reply = json.loads(response.stdout.splitlines()[-1]) if response.stdout.strip() else {}
        result = reply.get("result") or {}
        failed = response.returncode != 0 or result.get("isError") is not False
        payload = json.loads(result["content"][0]["text"]) if not failed else {}
        hits = payload.get("results") or []
        report = {"status": "passed" if not failed and hits else "failed", "scope": "public discovery through frozen native MCP, not model/research quality",
            "executor": str(args.executor.resolve()), "search_requests_max": 1, "model_requests": 0,
            "private_files_or_history_sent": False, "provider": payload.get("provider"), "results_count": len(hits),
            "source_hosts": sorted({urlsplit(h["url"]).hostname for h in hits}),
            "checks": {"actual_mcp_success": not failed, "public_results_returned": bool(hits), "discovery_only": payload.get("discovery_only") is True}}
        report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report))
        return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
