"""The packaged engine's tools speak UTF-8 both ways. A frozen program ignores PYTHONIOENCODING and uses the Windows
code page, which these tests stand in for with cp1252."""
import importlib
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from smara import native_management
from smara.native_goals import GoalStore

TEXT = "टीम की रिपोर्ट — ₹43,900 📒"
MODES = {"--native-team": "smara.native_team", "--native-docs": "smara.native_docs", "--native-tools": "smara.native_tools",
         "--native-computer": "smara.native_computer", "--native-browser": "smara.native_browser",
         "--native-teach": "smara.native_teach"}


@pytest.mark.parametrize("mode", MODES)
def test_every_packaged_tool_reads_and_writes_utf8(monkeypatch, mode):
    sent = (TEXT + "\n").encode("utf-8")
    received = io.BytesIO()
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(sent), encoding="cp1252", errors="surrogateescape"))
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(received, encoding="cp1252"))
    seen = []

    def echo(argv=None):
        seen.append(sys.stdin.readline())
        sys.stdout.write(seen[0])
        sys.stdout.flush()
        return 0

    monkeypatch.setattr(importlib.import_module(MODES[mode]), "main", echo)
    assert native_management.main([mode]) == 0
    assert seen == [TEXT + "\n"] and received.getvalue() == sent


def test_the_packaged_team_tool_handles_dashes_rupees_and_hindi(tmp_path, monkeypatch):
    """The three failures seen on the installed app: an unreadable reply, a stopped tool and a refused rule."""
    state = tmp_path / "desktop.json"
    state.write_text(json.dumps({"workspace": str(tmp_path)}))
    monkeypatch.setenv("SMARA_DESKTOP_STATE", str(state))
    monkeypatch.delenv("SMARA_NATIVE_HOME", raising=False)
    store = GoalStore()
    assert str(tmp_path) in str(store.path)
    for words in ("Read the dues in Downloads — the owner asked", "Ravi owes ₹12,500 — रवि"):
        store.action({"action": "create", "objective": words, "success_criteria": "x", "workspace": str(tmp_path),
                      "profile_id": "synthetic", "agent_id": "kiran"})
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"), "PYTHONIOENCODING": "cp1252",
           "PYTHONUTF8": "0", "SMARA_DESKTOP_STATE": str(state), "SMARA_AGENT_SKILLS_DIR": str(tmp_path / "skills")}
    env.pop("SMARA_TASK_ID", None)

    def request(number, method, params):
        return json.dumps({"jsonrpc": "2.0", "id": number, "method": method, "params": params}, ensure_ascii=False)

    asked = [request(1, "initialize", {}),
             request(2, "tools/call", {"name": "team_list", "arguments": {}}),
             request(3, "tools/call", {"name": "team_tasks", "arguments": {}}),
             request(4, "tools/call", {"name": "team_remember", "arguments": {"employee": "Kiran", "rule": TEXT}}),
             request(5, "ping", {})]
    done = subprocess.run([sys.executable, "-c", "from smara.native_management import main; raise SystemExit(main())",
                           "--native-team", "--workspace", str(tmp_path)], input=("\n".join(asked) + "\n").encode("utf-8"),
                          capture_output=True, env=env, timeout=120)
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")[-1500:]
    replies = {r["id"]: r for r in map(json.loads, done.stdout.decode("utf-8").splitlines())}     # every reply readable
    assert sorted(replies) == [1, 2, 3, 4, 5]                                                    # and the tool still running
    assert all(not replies[n]["result"]["isError"] for n in (2, 3, 4))
    assert "रवि" in replies[3]["result"]["content"][0]["text"]
    saved = json.loads((tmp_path / "native-settings.json").read_text(encoding="utf-8"))
    assert TEXT in next(a for a in saved["agents"] if a["id"] == "kiran")["instructions"]
