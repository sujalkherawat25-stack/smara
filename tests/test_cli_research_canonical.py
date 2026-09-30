import json

from smara import app_adapter
from smara.cli import main


def test_research_cli_delegates_to_same_canonical_app_adapter(tmp_path, monkeypatch, capsys):
    calls = []

    def fake_run(topic, **kwargs):
        calls.append((topic, kwargs))
        return {"status": "completed", "answer": "Evidence-backed answer", "research_review": {"claim_count": 1}}

    monkeypatch.setattr(app_adapter, "run_canonical_task", fake_run)
    result = main(["--workspace", str(tmp_path), "research", "current", "Python", "release", "--json"])
    assert result == 0
    assert calls == [("current Python release", {"workspace": tmp_path, "tool_profile": "research_web", "research_mode": "auto"})]
    assert json.loads(capsys.readouterr().out)["answer"] == "Evidence-backed answer"
