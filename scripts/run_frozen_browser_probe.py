"""Probe the actual frozen browser MCP driver; no AI, install or publication."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import queue
import subprocess
import threading
import uuid


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True, type=Path)
    args = parser.parse_args()
    binary = args.binary.resolve(strict=True)
    if not binary.is_file() or not binary.is_relative_to((root / "build").resolve()):
        raise ValueError("Select the newly frozen build probe, not a user executable")
    fixture = root / "build" / ("frozen-browser-check-" + uuid.uuid4().hex)
    workspace = fixture / "workspace"
    workspace.mkdir(parents=True)
    safe_environment = {key: value for key, value in os.environ.items() if key.upper() in {
        "SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "LOCALAPPDATA", "APPDATA",
        "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432", "COMSPEC", "ALLUSERSPROFILE"}}
    process = subprocess.Popen([str(binary), "--native-browser", "--workspace", str(workspace), "--origin", "https://docs.python.org"],
                               cwd=workspace, env=safe_environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, encoding="utf-8", creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    replies = queue.Queue()
    def read():
        try:
            for line in process.stdout:
                replies.put(json.loads(line))
        finally:
            replies.put(None)
    def drain():
        for _line in process.stderr:
            pass
    threading.Thread(target=read, daemon=True).start()
    threading.Thread(target=drain, daemon=True).start()
    counter = 0
    def rpc(method, params):
        nonlocal counter
        counter += 1
        process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": counter, "method": method, "params": params}) + "\n")
        process.stdin.flush()
        response = replies.get(timeout=60)
        if not response or response.get("id") != counter or "error" in response:
            raise RuntimeError("Frozen browser MCP failed or exited")
        result = response["result"]
        if result.get("isError"):
            raise RuntimeError("Frozen browser tool returned an error")
        return result
    report = {"status": "failed", "binary": str(binary), "sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
              "fixture": str(fixture), "paid_requests": 0, "source_python_on_child_path": False,
              "personal_browser_used": False, "installs_or_publishes": False,
              "scope": "frozen CLI browser MCP/Playwright packaging; not model quality or optimized native package acceptance"}
    try:
        rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "smara_frozen_probe", "version": "1"}})
        tools = rpc("tools/list", {})["tools"]
        result = rpc("tools/call", {"name": "browser_open", "arguments": {"url": "https://docs.python.org/3/library/heapq.html"}})
        observation = json.loads(result["content"][0]["text"])
        checks = {"browser_catalog_present": {entry["name"] for entry in tools} == {"browser_open", "browser_observe", "browser_text_page", "browser_act", "browser_close"},
                  "real_page_opened_from_frozen_driver": observation["url"] == "https://docs.python.org/3/library/heapq.html" and "heapq" in observation["title"],
                  "real_document_text_retrieved": "heappushpop" in observation["text"] and "heapreplace" in observation["text"],
                  "dom_grounding_present": bool(observation["observation_id"]) and observation["modality"] == "DOM text; no vision"}
        if observation["next_offset"] is not None:
            next_page = json.loads(rpc("tools/call", {"name": "browser_text_page", "arguments": {"observation_id": observation["observation_id"], "offset": observation["next_offset"]}})["content"][0]["text"])
            checks["cached_pagination_consistent"] = next_page["offset"] == len(observation["text"]) and next_page["total_chars"] == observation["total_chars"]
        closed = json.loads(rpc("tools/call", {"name": "browser_close", "arguments": {}})["content"][0]["text"])
        checks["owned_browser_closed"] = closed.get("closed") is True
        report.update(status="passed" if all(checks.values()) else "failed", checks=checks, title=observation["title"], observed_url=observation["url"])
    except Exception as exc:
        report["error_type"] = type(exc).__name__
    finally:
        if process.poll() is None:
            process.stdin.close()
            try:
                process.wait(timeout=45)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        report["process_exit_code"] = process.returncode
    (fixture / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report), flush=True)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
