from smara.cli import build_parser, main


def test_cli_accepts_live_web_profile_aliases():
    parser = build_parser()
    for alias in ("research-web", "research_web", "live-web"):
        args = parser.parse_args(["run", "research this", "--json", "--tool-profile", alias])
        assert args.tool_profile == alias


def test_application_adapter_persists_canonical_live_web_profile(tmp_path, monkeypatch):
    import smara.app_adapter as adapter

    observed = []

    def fake_run(self, task, max_iterations):
        observed.append((self.toolset, self.session_engine.get("tool_profile")))
        return {"session": {"status": "completed", "answer": "ok", "unresolved_items": []}}

    monkeypatch.setattr(adapter.SmaraAutonomousAgent, "run", fake_run)
    adapter.run_canonical_task(
        "research objective",
        tmp_path,
        session_id="live-web-cli-profile",
        budget_profile="short",
        tool_profile="live-web",
    )
    adapter.run_canonical_task(
        "resume objective",
        tmp_path,
        session_id="live-web-cli-profile",
        budget_profile="short",
        tool_profile="full",
    )

    assert observed == [("research_web", "research_web"), ("research_web", "research_web")]


def test_cli_run_uses_live_web_profile_without_json_mode(tmp_path, monkeypatch, capsys):
    import smara.autonomous_agent as autonomous_agent

    observed = {}

    def fake_run(self, task, max_iterations):
        observed["toolset"] = self.toolset
        self.session_engine.begin_incremental(task)
        result = self.session_engine.finish_incremental("completed", "live result")
        return {"session": result}

    monkeypatch.setattr(autonomous_agent.SmaraAutonomousAgent, "run", fake_run)
    code = main([
        "run",
        "research objective",
        "--workspace",
        str(tmp_path),
        "--tool-profile",
        "research-web",
    ])

    assert code == 0
    assert observed["toolset"] == "research_web"
    output = capsys.readouterr().out
    assert "live result" in output
    assert "resume with: smara resume" in output
