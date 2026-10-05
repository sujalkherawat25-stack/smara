"""Opt-in live stdio acceptance using a synthetic prompt in an empty workspace."""
import argparse
import json
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.output.resolve()
    if root.exists():
        parser.error("Use a fresh evidence directory")
    root.mkdir(parents=True)
    workspace = root / "workspace"
    workspace.mkdir()
    events = queue.Queue()
    frames = []
    with (root / "stderr.log").open("w", encoding="utf-8") as errors:
        process = subprocess.Popen([sys.executable, "-m", "smara.cli", "--workspace", str(workspace),
                                    "--model", "sarvam_glm", "app-server"], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=errors, text=True, encoding="utf-8")
        def read():
            for line in process.stdout:
                events.put(json.loads(line))
        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        def send(ident, method, params=None):
            process.stdin.write(json.dumps({"id": ident, "method": method, "params": params or {}}) + "\n")
            process.stdin.flush()
        def until(check):
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                frame = events.get(timeout=max(.1, deadline-time.monotonic()))
                frames.append(frame)
                if "error" in frame:
                    raise RuntimeError(str(frame["error"]))
                if check(frame):
                    return frame
            raise RuntimeError("Timed out waiting for session protocol")
        try:
            started = time.monotonic()
            send(1, "initialize")
            until(lambda frame: frame.get("id") == 1)
            send(2, "thread/create", {"thread_id": "synthetic-live"})
            until(lambda frame: frame.get("id") == 2)
            send(3, "turn/start", {"thread_id": "synthetic-live", "tool_profile": "coding",
                "request": "This is a synthetic protocol acceptance check. Do not read files, run commands or call tools. Reply exactly: FINAL ANSWER: SESSION_OK"})
            until(lambda frame: frame.get("method") == "session/event" and frame["params"]["kind"] == "turn.completed")
            send(4, "thread/read", {"thread_id": "synthetic-live"})
            final = until(lambda frame: frame.get("id") == 4)["result"]
            answer = final["thread"]["result"].get("answer", "")
            passed = final["thread"]["status"] == "completed" and "SESSION_OK" in answer and any(item["kind"] == "agent_message" for item in final["items"])
            report = {"passed": passed, "status": final["thread"]["status"], "answer": answer,
                      "seconds": round(time.monotonic()-started, 2), "turn_id": final["turns"][0]["turn_id"],
                      "cursor": final["cursor"], "event_kinds": [event["kind"] for event in final["events"]]}
            (root / "result.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(json.dumps(report))
            return 0 if passed else 1
        finally:
            (root / "frames.json").write_text(json.dumps(frames, indent=2), encoding="utf-8")
            process.stdin.close()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=10)


if __name__ == "__main__":
    raise SystemExit(main())
