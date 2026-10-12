"""Protocol tests use synthetic provider streams, not live-model quality evidence."""
import json
import os
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from smara.native_provider import ChatEndpoint, ResponsesAdapter, UnsupportedWireFeature, response_tool, translate_request
from smara.native_runtime import active_profile, launch_options, native_binary


def test_preserves_long_input_and_tool_history():
    long_text = "synthetic input " * 20000
    request = {"instructions": "system", "input": [
        {"role": "user", "content": [{"type": "input_text", "text": long_text}]},
        {"type": "function_call", "name": "exec_command", "namespace": "functions", "call_id": "c1", "arguments": '{"cmd":"echo hello"}'},
        {"type": "function_call_output", "call_id": "c1", "output": "hello"},
    ], "tools": [{"type": "namespace", "name": "functions", "tools": [{"type": "function", "name": "exec_command", "parameters": {"type": "object"}}]}]}
    payload, mapping = translate_request(request, "test-model")
    assert payload["messages"][1]["content"][0]["text"] == long_text
    assert payload["messages"][2]["tool_calls"][0]["function"]["name"] == "functions__exec_command"
    assert payload["messages"][3] == {"role": "tool", "tool_call_id": "c1", "content": "hello"}
    item = response_tool({"id": "c2", "function": {"name": "functions__exec_command", "arguments": '{"cmd":"pwd"}'}}, mapping)
    assert item["namespace"] == "functions" and item["name"] == "exec_command"


def test_sarvam_glm_gets_bounded_long_output_budget_instead_of_chat_default():
    endpoint = ChatEndpoint("https://api.sarvam.ai/v2", "glm5.3", "synthetic-key")
    payload, _ = translate_request(
        {"input": "Create and verify a requested artifact", "reasoning": {"effort": "high"}},
        endpoint.model,
        default_max_output_tokens=endpoint.default_max_output_tokens,
        sarvam=True,
    )
    assert endpoint.default_max_output_tokens == 16_384
    assert payload["max_tokens"] == 16_384
    assert payload["reasoning_effort"] == "high"


def test_explicit_output_budget_wins_and_other_providers_keep_their_defaults():
    endpoint = ChatEndpoint("https://api.sarvam.ai/v2", "glm5.3", "synthetic-key")
    payload, _ = translate_request(
        {"input": "synthetic", "max_output_tokens": 9000}, endpoint.model,
        default_max_output_tokens=endpoint.default_max_output_tokens,
        sarvam=True,
    )
    assert payload["max_tokens"] == 9000
    other, _ = translate_request({"input": "synthetic"}, "other-model")
    assert "max_tokens" not in other


@pytest.mark.parametrize(("codex_effort", "sarvam_effort"), [
    ("minimal", "low"), ("low", "low"), ("medium", "high"),
    ("high", "high"), ("xhigh", "max"), ("ultra", "max"), ("max", "max"),
])
def test_codex_reasoning_effort_maps_to_sarvam_supported_values(codex_effort, sarvam_effort):
    payload, _ = translate_request(
        {"input": "synthetic", "reasoning": {"effort": codex_effort}},
        "deepseekv4.1-flash", sarvam=True,
    )
    assert payload["reasoning_effort"] == sarvam_effort


def test_reasoning_none_disables_deepseek_thinking_but_uses_glm_lowest_effort():
    deepseek, _ = translate_request(
        {"input": "synthetic", "reasoning": {"effort": "none"}},
        "deepseekv4.1-flash", sarvam=True,
    )
    glm, _ = translate_request(
        {"input": "synthetic", "reasoning": {"effort": "none"}},
        "glm5.3", sarvam=True,
    )
    assert deepseek["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}
    assert glm["reasoning_effort"] == "low"
    assert "extra_body" not in glm


def test_codex_output_schema_is_translated_to_sarvam_structured_output():
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}
    payload, _ = translate_request({"input": "synthetic", "text": {"format": {
        "type": "json_schema", "name": "codex_output_schema", "strict": True, "schema": schema,
    }}}, "glm5.3", sarvam=True)
    assert payload["response_format"] == {"type": "json_schema", "json_schema": {
        "name": "codex_output_schema", "strict": True, "schema": schema,
    }}


def test_sarvam_image_inputs_are_restricted_to_gemma_inline_data_urls():
    image = {"type": "input_image", "image_url": "data:image/png;base64,c3ludGhldGlj"}
    payload, _ = translate_request({"input": [{"type": "message", "role": "user", "content": [image]}]}, "gemma4", sarvam=True)
    assert payload["messages"][0]["content"][0]["image_url"]["url"] == image["image_url"]
    with pytest.raises(UnsupportedWireFeature, match="requires Gemma 4"):
        translate_request({"input": [{"type": "message", "role": "user", "content": [image]}]}, "glm5.3", sarvam=True)
    remote = {"type": "input_image", "image_url": "https://example.test/image.png"}
    with pytest.raises(UnsupportedWireFeature, match="inline base64"):
        translate_request({"input": [{"type": "message", "role": "user", "content": [remote]}]}, "gemma4", sarvam=True)


@pytest.mark.parametrize(("model", "context"), [
    ("glm5.3", 1_048_576), ("deepseekv4.1-flash", 1_048_576),
    ("gemma4", 131_072), ("sarvam-105b", 131_072),
    ("sarvam-105b-conversations", 32_768),
])
def test_sarvam_context_metadata_matches_current_model_ids(model, context):
    endpoint = ChatEndpoint("https://api.sarvam.ai/v2", model, "") if model != "sarvam-105b-conversations" else ChatEndpoint("https://api.sarvam.ai/v1", model, "")
    assert endpoint.effective_context_window == context


def test_sarvam_context_override_cannot_exceed_published_model_limit():
    endpoint = ChatEndpoint("https://api.sarvam.ai/v2", "sarvam-105b", "", context_window=500_000)
    assert endpoint.effective_context_window == 131_072
    assert ChatEndpoint("https://api.sarvam.ai/v2", "deepseekv4-flash", "").model == "deepseekv4.1-flash"
    assert ChatEndpoint("https://api.sarvam.ai/v2", "deepseekv4-flash", "").effective_context_window == 1_048_576


def test_sarvam_routes_and_models_are_exact_and_conversation_v1_is_preserved():
    endpoint = ChatEndpoint("https://api.sarvam.ai/v1/chat/completions", "sarvam-105b-conversations", "")
    assert endpoint.url == "https://api.sarvam.ai/v1/chat/completions"
    with pytest.raises(ValueError, match="available on /v1 only"):
        ChatEndpoint("https://api.sarvam.ai/v2", "sarvam-105b-conversations", "")
    with pytest.raises(ValueError, match="exact Sarvam Chat Completions base URL"):
        ChatEndpoint("https://api.sarvam.ai/v2/proxy", "deepseekv4.1-flash", "")
    with pytest.raises(ValueError, match="not available on /v2"):
        ChatEndpoint("https://api.sarvam.ai/v2", "glm5.2", "")


@pytest.mark.parametrize(("base_url", "model", "message"), [
    ("https://api.sarvam.ai/v2/responses", "glm5.3", "translates through Chat Completions"),
    ("https://api.sarvam.ai/v1", "glm5.3", "requires the /v2"),
    ("https://api.sarvam.ai/v2", "sarvam-105b-conversations", "available on /v1 only"),
])
def test_sarvam_route_model_mismatch_fails_with_repair_guidance(base_url, model, message):
    with pytest.raises(ValueError, match=message):
        ChatEndpoint(base_url, model, "")


@pytest.mark.parametrize("invalid", [0, -1, True, "16384"])
def test_invalid_output_budget_fails_closed(invalid):
    with pytest.raises(UnsupportedWireFeature, match="positive integer"):
        translate_request({"input": "synthetic", "max_output_tokens": invalid}, "test-model")


def test_freeform_patch_is_exact_and_not_executed_by_adapter():
    patch = "*** Begin Patch\n*** Add File: hello.txt\n+hello\n*** End Patch"
    payload, mapping = translate_request({"input": "patch", "tools": [{"type": "custom", "name": "apply_patch", "format": {"type": "grammar", "definition": "start: PATCH"}}]}, "test")
    assert "Tool input grammar" in payload["tools"][0]["function"]["description"]
    item = response_tool({"id": "call-patch", "function": {"name": "apply_patch", "arguments": json.dumps({"input": patch})}}, mapping)
    assert item["type"] == "custom_tool_call" and item["input"] == patch


@pytest.mark.parametrize("kind", ["web_search", "computer", "tool_search", "file_search"])
def test_unsupported_hosted_tools_fail_closed(kind):
    with pytest.raises(UnsupportedWireFeature):
        translate_request({"tools": [{"type": kind}], "input": "test"}, "test")


def test_unknown_or_invalid_tool_cannot_be_sent_to_executor():
    _, mapping = translate_request({"tools": [{"type": "function", "name": "read"}], "input": "test"}, "test")
    with pytest.raises(UnsupportedWireFeature):
        response_tool({"id": "c", "function": {"name": "write", "arguments": "{}"}}, mapping)
    with pytest.raises(json.JSONDecodeError):
        response_tool({"id": "c", "function": {"name": "read", "arguments": "{unfinished"}}, mapping)


def stream_chunks(*chunks, done=True):
    lines = ["data: " + json.dumps(chunk) for chunk in chunks]
    if done:
        lines.append("data: [DONE]")
    return SimpleNamespace(iter_lines=lambda: iter(lines))


def test_stream_reports_actual_deltas_and_usage():
    with ResponsesAdapter(ChatEndpoint("http://127.0.0.1", "test", "")) as adapter:
        events = []
        handler = SimpleNamespace(_event=events.append)
        stream = stream_chunks(
            {"choices": [{"delta": {"content": "hello "}}]},
            {"choices": [{"delta": {"content": "world"}, "finish_reason": "stop"}]},
            {"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}},
        )
        adapter._stream(stream, handler, {})
        assert "".join(event["delta"] for event in events if event["type"] == "response.output_text.delta") == "hello world"
        assert events[-1]["response"]["usage"]["total_tokens"] == 12
        assert events[-1]["response"]["output"][0]["content"][0]["text"] == "hello world"


def test_stream_does_not_leak_sarvam_reasoning_trace_to_user_text():
    with ResponsesAdapter(ChatEndpoint("http://127.0.0.1", "test", "")) as adapter:
        events = []
        adapter._stream(stream_chunks(
            {"choices": [{"delta": {"reasoning_content": "private synthetic reasoning"}}]},
            {"choices": [{"delta": {"content": "Visible answer"}, "finish_reason": "stop"}]},
        ), SimpleNamespace(_event=events.append), {})
        visible = "".join(event.get("delta", "") for event in events if event["type"] == "response.output_text.delta")
        assert visible == "Visible answer"
        assert "private synthetic reasoning" not in json.dumps(events)


def test_content_filter_is_reported_as_a_provider_filter_not_transport_failure():
    with ResponsesAdapter(ChatEndpoint("http://127.0.0.1", "test", "")) as adapter:
        events = []
        adapter._stream(stream_chunks({"choices": [{"delta": {}, "finish_reason": "content_filter"}]}), SimpleNamespace(_event=events.append), {})
        assert events[-1]["type"] == "response.incomplete"
        assert events[-1]["response"]["incomplete_details"]["reason"] == "content_filter"


def test_cut_off_stream_is_not_success():
    with ResponsesAdapter(ChatEndpoint("http://127.0.0.1", "test", "")) as adapter:
        events = []
        with pytest.raises(UnsupportedWireFeature):
            adapter._stream(stream_chunks({"choices": [{"delta": {"content": "partial"}}]}, done=False), SimpleNamespace(_event=events.append), {})
        assert not any(event["type"] == "response.completed" for event in events)


def test_freeform_stream_preserves_native_patch_deltas_and_final_input():
    patch = "*** Begin Patch\n*** Add File: synthetic.txt\n+fixture\n*** End Patch"
    _, mapping = translate_request({"tools": [{"type": "custom", "name": "apply_patch"}], "input": "synthetic"}, "test")
    with ResponsesAdapter(ChatEndpoint("http://127.0.0.1", "test", "")) as adapter:
        events = []
        adapter._stream(stream_chunks({"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "patch-call", "function": {"name": "apply_patch", "arguments": json.dumps({"input": patch})}}]}, "finish_reason": "tool_calls"}]}), SimpleNamespace(_event=events.append), mapping)
        assert events[1]["item"]["input"] == ""
        assert events[2]["type"] == "response.custom_tool_call_input.delta"
        assert events[2]["delta"] == patch
        assert events[3]["item"]["input"] == patch


def test_provider_refusal_is_visible_and_missing_usage_is_unknown():
    with ResponsesAdapter(ChatEndpoint("http://127.0.0.1", "test", "")) as adapter:
        events = []
        adapter._stream(stream_chunks({"choices": [{"delta": {"refusal": "I cannot help with this request."}, "finish_reason": "stop"}]}), SimpleNamespace(_event=events.append), {})
        response = events[-1]["response"]
        assert response["output"][0]["content"][0]["text"] == "I cannot help with this request."
        assert response["usage"] is None
        assert all(item["type"] == "message" for item in response["output"])


def test_length_limited_arguments_are_not_executed():
    with ResponsesAdapter(ChatEndpoint("http://127.0.0.1", "test", "")) as adapter:
        events = []
        adapter._stream(stream_chunks({"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c", "function": {"name": "exec", "arguments": "{incomplete"}}]}, "finish_reason": "length"}]}), SimpleNamespace(_event=events.append), {})
        assert events[-1]["type"] == "response.incomplete"
        assert not any(event["type"] == "response.output_item.done" for event in events)


def test_loopback_auth_and_route_are_required():
    with ResponsesAdapter(ChatEndpoint("http://127.0.0.1", "test", "provider-secret")) as adapter:
        with httpx.Client(trust_env=False) as client:
            response = client.post(adapter.base_url + "/responses", json={"stream": True, "input": "private"})
            assert response.status_code == 401
            response = client.post(adapter.base_url + "/other", headers={"Authorization": "Bearer " + adapter.token}, json={})
            assert response.status_code == 404
            response = client.post(adapter.base_url + "/responses", headers={"Authorization": "Bearer " + adapter.token}, json={"stream": True, "tools": [{"type": "computer"}]})
            assert response.status_code == 400
            assert "provider-secret" not in response.text


def test_rejected_posts_return_json_repeatedly_without_reset_or_inference():
    with ResponsesAdapter(ChatEndpoint("http://127.0.0.1:1", "test", "provider-secret")) as adapter:
        with httpx.Client(trust_env=False) as client:
            for _ in range(20):
                response = client.post(adapter.base_url + "/responses", json={"input": "synthetic rejected body"})
                assert response.status_code == 401
                assert response.headers["connection"] == "close"
                assert response.json()["error"]["message"] == "Loopback authentication required"
                response = client.post(adapter.base_url + "/other", headers={"Authorization": "Bearer " + adapter.token}, json={"input": "synthetic rejected body"})
                assert response.status_code == 404
                assert response.headers["connection"] == "close"
                assert "provider-secret" not in response.text


def test_credentials_not_in_native_command_or_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("SARVAM_API_KEY", "private-provider-key")
    monkeypatch.setenv("SMARA_MODEL_SARVAM_GLM_API_KEY", "private-provider-key")
    monkeypatch.setenv("XAI_API_KEY", "other-provider-key")
    monkeypatch.setenv("TAVILY_API_KEY", "private-search-key")
    monkeypatch.setenv("GITHUB_TOKEN", "private-github-token")
    monkeypatch.setenv("SMARA_SEARCH_PROVIDER", "tavily")
    monkeypatch.setenv("CODEX_HOME", "unrelated-codex-home")
    adapter = ResponsesAdapter(ChatEndpoint("https://example.com/v2", "test", "private-provider-key"))
    try:
        argv, env = launch_options(adapter, home=tmp_path / "smara-home", workspace=tmp_path)
        assert "private-provider-key" not in " ".join(argv)
        assert "SARVAM_API_KEY" not in env
        assert "SMARA_MODEL_SARVAM_GLM_API_KEY" not in env
        assert "XAI_API_KEY" not in env
        assert "TAVILY_API_KEY" not in env and "GITHUB_TOKEN" not in env
        assert "SMARA_SEARCH_PROVIDER" not in env
        assert env["SMARA_NATIVE_WIRE_TOKEN"] == adapter.token
        assert env["CODEX_HOME"] == str((tmp_path / "smara-home").resolve())
        assert 'approval_policy="on-request"' in argv
        assert 'sandbox_mode="workspace-write"' in argv
        assert ('features.prefer_mxc=false' in argv) is (os.name == "nt")
    finally:
        adapter.server.server_close()
        adapter.client.close()


def test_native_resolution_does_not_search_path(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(RuntimeError, match="no legacy or installed-Codex fallback"):
        native_binary(str(tmp_path / "missing.exe"))


def test_provider_context_metadata_is_explicit_not_gpt_fallback():
    assert ChatEndpoint("https://api.sarvam.ai/v2", "glm5.3", "").effective_context_window == 1_048_576
    assert ChatEndpoint("https://example.com/v1", "glm5.3", "").effective_context_window is None
    assert ChatEndpoint("http://127.0.0.1", "custom", "", context_window=65536).effective_context_window == 65536
    with pytest.raises(ValueError):
        ChatEndpoint("http://127.0.0.1", "custom", "", context_window=True)


@pytest.mark.parametrize("frozen", [False, True])
@pytest.mark.parametrize("saved_enabled", [False, True])
def test_standard_tools_launch_without_opt_in_but_memory_remains_opt_in(tmp_path, monkeypatch, frozen, saved_enabled):
    import sys
    import tomllib

    monkeypatch.setattr(sys, "frozen", frozen, raising=False)
    workspace = tmp_path / "workspace स्मारा 😀"
    workspace.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    saved = home / "config.toml"
    saved.write_text('[mcp_servers.smara_readers]\nenabled = ' + str(saved_enabled).lower() + '\ncommand = "saved-transport"\n', encoding="utf-8")
    original = saved.read_bytes()
    adapter = ResponsesAdapter(ChatEndpoint("http://127.0.0.1", "custom", ""))
    try:
        off, _ = launch_options(adapter, home=home, workspace=workspace)
        on, _ = launch_options(adapter, home=home, workspace=workspace, tools_enabled=True)
        expected_args = (["--native-tools"] if frozen else ["-m", "smara.native_tools"]) + ["--workspace", str(workspace.resolve())]
        for options, extra in ((off, ["--context-only"]), (on, [])):
            overrides = tomllib.loads("\n".join(options[1::2]))["mcp_servers"]["smara_readers"]
            assert overrides["enabled"] is True
            assert overrides["command"] == sys.executable
            assert overrides["args"] == expected_args + extra
            assert overrides["tools"]["document_ocr"]["approval_mode"] == "prompt"
            assert overrides["tools"]["image_analyze"]["approval_mode"] == "prompt"
        assert 'mcp_servers.smara_readers.enabled=true' in on
        assert 'mcp_servers.smara_readers.env.PYTHONIOENCODING="utf-8"' in on
        assert "features.plugins=false" in on
        assert saved.read_bytes() == original
    finally:
        adapter.server.server_close()
        adapter.client.close()


def test_saved_search_key_exposes_public_search_without_project_memory(tmp_path):
    import sys
    import tomllib

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    home = tmp_path / "home"
    adapter = ResponsesAdapter(ChatEndpoint("http://127.0.0.1", "custom", ""))
    try:
        options, _ = launch_options(adapter, home=home, workspace=workspace, search_enabled=True)
        reader = tomllib.loads("\n".join(options[1::2]))["mcp_servers"]["smara_readers"]
        expected = (["--native-tools"] if getattr(sys, "frozen", False) else ["-m", "smara.native_tools"])
        expected += ["--workspace", str(workspace.resolve()), "--standard-tools"]
        assert reader["enabled"] is True
        assert reader["args"] == expected
    finally:
        adapter.server.server_close()
        adapter.client.close()


def test_search_reader_gets_vault_path_but_never_inherits_provider_secret(tmp_path, monkeypatch):
    import tomllib

    state = tmp_path / "desktop.json"
    vault = tmp_path / "credentials.json"
    monkeypatch.setenv("SMARA_DESKTOP_STATE", str(state))
    monkeypatch.setenv("SMARA_DESKTOP_CREDENTIALS", str(vault))
    monkeypatch.setenv("TAVILY_API_KEY", "synthetic-private-search-key")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    adapter = ResponsesAdapter(ChatEndpoint("http://127.0.0.1", "custom", ""))
    try:
        options, environment = launch_options(adapter, home=tmp_path / "home", workspace=workspace, search_enabled=True)
        config = tomllib.loads("\n".join(options[1::2]))
        reader_env = config["mcp_servers"]["smara_readers"]["env"]
        assert reader_env["SMARA_DESKTOP_STATE"] == str(state.resolve())
        assert reader_env["SMARA_DESKTOP_CREDENTIALS"] == str(vault.resolve())
        assert "TAVILY_API_KEY" not in environment
        assert "synthetic-private-search-key" not in json.dumps(options) + json.dumps(environment)
    finally:
        adapter.server.server_close()
        adapter.client.close()


def test_search_credential_readiness_is_local_and_fail_safe(monkeypatch):
    from smara.native_runtime import _search_credential_available

    monkeypatch.setattr("smara.native_search.search_status", lambda **kwargs: {"configured": True, "network_tested": False})
    assert _search_credential_available() is True
    monkeypatch.setattr("smara.native_search.search_status", lambda **kwargs: {"configured": False})
    assert _search_credential_available() is False
    monkeypatch.setattr("smara.native_search.search_status", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("synthetic")))
    assert _search_credential_available() is False


def test_cli_enables_saved_search_readers_without_enabling_memory(tmp_path, monkeypatch):
    import smara.native_runtime as runtime

    captured = {}
    class Adapter:
        def __init__(self, endpoint): pass
        def __enter__(self): return self
        def __exit__(self, *_args): return False

    monkeypatch.setattr(runtime, "native_binary", lambda: tmp_path / "smara-native.exe")
    monkeypatch.setattr(runtime, "runtime_home", lambda: tmp_path / "home")
    monkeypatch.setattr(runtime, "_search_credential_available", lambda: True)
    monkeypatch.setattr(runtime, "ResponsesAdapter", Adapter)
    monkeypatch.setattr("smara.native_profiles.load_profiles", lambda: ([{"id": "synthetic", "base_url": "http://127.0.0.1", "model": "synthetic"}], "synthetic", {}))
    monkeypatch.setattr("smara.native_profiles.resolve_profile_key", lambda *_args: "")
    monkeypatch.setattr(runtime, "launch_options", lambda _adapter, **kwargs: (captured.update(kwargs) or [], {}))
    monkeypatch.setattr(runtime.subprocess, "call", lambda *_args, **_kwargs: 0)
    assert runtime.main([]) == 0
    assert captured["search_enabled"] is True
    assert captured["tools_enabled"] is False


def test_isolated_workers_require_explicit_launch_opt_in(tmp_path, monkeypatch):
    monkeypatch.setenv("SMARA_NATIVE_WORKTREE_WORKERS", "1")
    adapter = ResponsesAdapter(ChatEndpoint("http://127.0.0.1", "synthetic", ""))
    try:
        off, environment = launch_options(adapter, home=tmp_path / "home", workspace=tmp_path)
        assert "SMARA_NATIVE_WORKTREE_WORKERS" not in environment
        assert 'features.multi_agent_v2=false' not in off
        on, environment = launch_options(adapter, home=tmp_path / "home", workspace=tmp_path, workers_enabled=True)
        assert environment["SMARA_NATIVE_WORKTREE_WORKERS"] == "1"
        assert 'features.multi_agent_v2=false' in on
        assert 'approval_policy="on-request"' in on
        assert 'sandbox_mode="workspace-write"' in on
    finally:
        adapter.server.server_close()
        adapter.client.close()


def test_native_browser_is_opt_in_and_each_tool_requires_prompt(tmp_path, monkeypatch):
    monkeypatch.setattr("smara.native_browser._is_public_http_url", lambda url: url == "https://fixture.example")
    adapter = ResponsesAdapter(ChatEndpoint("http://127.0.0.1", "synthetic", ""))
    try:
        off, _ = launch_options(adapter, home=tmp_path / "home", workspace=tmp_path)
        assert "mcp_servers.smara_browser.enabled=false" in off
        # enabled=false alone creates an invalid transport and breaks every
        # ordinary native launch, even without any browser request.
        import sys
        assert "mcp_servers.smara_browser.command=" + json.dumps(sys.executable) in off
        assert 'mcp_servers.smara_browser.args=["-m", "smara.native_browser"]' in off
        on, _ = launch_options(adapter, home=tmp_path / "home", workspace=tmp_path, browser_origins=["https://fixture.example"])
        assert "mcp_servers.smara_browser.enabled=true" in on
        for name in ("browser_open", "browser_observe", "browser_act", "browser_text_page", "browser_close"):
            assert f'mcp_servers.smara_browser.tools.{name}.approval_mode="prompt"' in on
        with pytest.raises(ValueError):
            launch_options(adapter, home=tmp_path / "home", workspace=tmp_path, browser_origins=["http://private/"])
    finally:
        adapter.server.server_close(); adapter.client.close()


@pytest.mark.parametrize("profiles, selected", [([], "missing"), ([{"id": "other"}], "missing"), ([{"id": "same"}, {"id": "same"}], "same")])
def test_native_model_selection_never_silently_substitutes(profiles, selected):
    with pytest.raises(RuntimeError, match="not silently substitute"):
        active_profile(profiles, selected)


def test_desktop_console_entrypoint_uses_native_bootstrap(monkeypatch):
    from smara import desktop_executor, native_runtime

    monkeypatch.setattr(native_runtime, "serve_bootstrap", lambda: 17)
    monkeypatch.setattr(desktop_executor, "_main", lambda _argv: pytest.fail("Legacy executor entered"))
    assert desktop_executor.main(["--native-app-server"]) == 17


@pytest.mark.parametrize("url", ["http://remote.example/v1", "https://user:password@example.com/v1", "https://example.com/v1?key=secret"])
def test_provider_url_cannot_leak_key_to_unsafe_endpoint(url):
    with pytest.raises(ValueError):
        ChatEndpoint(url, "test", "secret")


def test_full_source_import_preserves_runtime_and_sandbox():
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / "native" / "UPSTREAM.json").read_text())
    assert len(manifest["commit"]) == 40
    source = root / "vendor" / "codex"
    for component in ("core", "app-server", "exec-server", "execpolicy", "sandboxing", "linux-sandbox", "windows-sandbox-rs", "tui", "protocol", "rollout", "rmcp-client"):
        assert (source / "codex-rs" / component / "Cargo.toml").is_file()
    assert "Apache License" in (source / "LICENSE").read_text()
    assert (source / "NOTICE").is_file()
