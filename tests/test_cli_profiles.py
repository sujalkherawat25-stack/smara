from smara.cli import build_parser, main


def test_cli_tools_lists_canonical_tools_without_entering_repl(tmp_path, capsys):
    code = main(["--plain", "--workspace", str(tmp_path), "tools"])

    assert code == 0
    payload = __import__("json").loads(capsys.readouterr().out)
    assert payload["count"] == len(payload["tools"])
    assert {item["name"] for item in payload["tools"]} >= {"file_read", "research_search", "terminal"}


def test_cli_models_lists_profiles_without_credential_values(tmp_path, monkeypatch, capsys):
    import json

    monkeypatch.setenv("SMARA_DESKTOP_STATE", str(tmp_path / "desktop.json"))
    code = main(["--plain", "--workspace", str(tmp_path), "models"])

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["profiles"]
    assert any(profile["active"] for profile in payload["profiles"])
    assert all("api_key" not in profile for profile in payload["profiles"])


def test_cli_ask_accepts_one_plain_final_model_reply(tmp_path, monkeypatch, capsys):
    from smara.autonomous_agent import SmaraAutonomousAgent

    calls = []
    monkeypatch.setattr("smara.cli._resolve_profile_key", lambda *_args: "fixture-key")

    def reply(_self, _messages, tools=None, max_tokens=16384):
        calls.append(1)
        return {"choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}]}

    monkeypatch.setattr(SmaraAutonomousAgent, "_call_model_api", reply)
    code = main(["--workspace", str(tmp_path), "ask", "Reply with OK."])

    assert code == 0
    assert capsys.readouterr().out.strip() == "OK"
    assert len(calls) == 1


def test_cli_accepts_live_web_profile_aliases():
    parser = build_parser()
    for alias in ("research-web", "research_web", "live-web"):
        args = parser.parse_args(["run", "research this", "--json", "--tool-profile", alias])
        assert args.tool_profile == alias
        assert args.research_mode == "auto"


def test_cli_preserves_and_normalizes_sarvam_glm_profile(tmp_path, monkeypatch):
    import json
    from smara.cli import _load_local_profiles

    state = tmp_path / "desktop.json"
    state.write_text(
        json.dumps({
            "model_profiles": [{
                "id": "sarvam",
                "label": "Sarvam GLM-5.2",
                "base_url": "https://api.sarvam.ai/v2",
                "model": "glm5.2",
                "auth_header": "api-subscription-key",
            }],
            "active_model": "sarvam",
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("SMARA_DESKTOP_STATE", str(state))
    profiles, active, _ = _load_local_profiles()

    assert active == "sarvam"
    assert profiles[0]["model"] == "glm5.2"
    assert profiles[0]["label"] == "Sarvam GLM 5.2"


def test_cli_default_profiles_include_sarvam_glm_v2(tmp_path, monkeypatch):
    from smara.cli import _load_local_profiles

    state = tmp_path / "desktop.json"
    monkeypatch.setenv("SMARA_DESKTOP_STATE", str(state))
    profiles, _, _ = _load_local_profiles()
    glm = next(profile for profile in profiles if profile["id"] == "sarvam_glm")
    assert glm["base_url"] == "https://api.sarvam.ai/v2"
    assert glm["model"] == "glm5.3"
    assert glm["auth_header"] == "api-subscription-key"


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


def test_application_adapter_auto_routes_research_and_selects_lane_budget(tmp_path, monkeypatch):
    import smara.app_adapter as adapter

    observed = {}

    def fake_run(self, task, max_iterations):
        observed.update(toolset=self.toolset, requested_mode=self.research_mode, budget=self.session_engine.budget)
        return {"session": {"status": "completed", "answer": "ok", "unresolved_items": []}}

    monkeypatch.setattr(adapter.SmaraAutonomousAgent, "run", fake_run)
    adapter.run_canonical_task(
        "Conduct deep research on the competitive market landscape and produce a comprehensive report",
        tmp_path,
    )
    assert observed["toolset"] == "research_web"
    assert observed["requested_mode"] == "auto"
    assert observed["budget"].wall_seconds == 1800
    assert observed["budget"].model_calls == 150


def test_cli_run_auto_routes_deep_research_without_profile_flag(tmp_path, monkeypatch, capsys):
    import smara.autonomous_agent as autonomous_agent

    observed = {}

    def fake_run(self, task, max_iterations):
        observed.update(toolset=self.toolset, budget=self.session_engine.budget)
        return {"session": {"status": "completed", "answer": "ok", "unresolved_items": []}}

    monkeypatch.setattr(autonomous_agent.SmaraAutonomousAgent, "run", fake_run)
    code = main([
        "run",
        "Prepare a comprehensive market comparison report with sources",
        "--workspace",
        str(tmp_path),
    ])
    assert code == 0
    assert observed["toolset"] == "research_web"
    assert observed["budget"].wall_seconds == 1800
    assert "ok" in capsys.readouterr().out
