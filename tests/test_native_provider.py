"""Protocol tests use synthetic provider streams, not live-model quality evidence."""
import json
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
    monkeypatch.setenv("CODEX_HOME", "unrelated-codex-home")
    adapter = ResponsesAdapter(ChatEndpoint("https://example.com/v2", "test", "private-provider-key"))
    try:
        argv, env = launch_options(adapter, home=tmp_path / "smara-home", workspace=tmp_path)
        assert "private-provider-key" not in " ".join(argv)
        assert "SARVAM_API_KEY" not in env
        assert "SMARA_MODEL_SARVAM_GLM_API_KEY" not in env
        assert "XAI_API_KEY" not in env
        assert env["CODEX_HOME"] == str((tmp_path / "smara-home").resolve())
        assert 'approval_policy="on-request"' in argv
        assert 'sandbox_mode="workspace-write"' in argv
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


def test_reader_launch_is_opt_in_and_has_valid_toml_overrides(tmp_path):
    adapter = ResponsesAdapter(ChatEndpoint("http://127.0.0.1", "custom", ""))
    try:
        off, _ = launch_options(adapter, home=tmp_path / "home", workspace=tmp_path)
        assert not any("mcp_servers.smara_readers" in option for option in off)
        on, _ = launch_options(adapter, home=tmp_path / "home", workspace=tmp_path, tools_enabled=True)
        assert 'mcp_servers.smara_readers.env.PYTHONIOENCODING="utf-8"' in on
        assert "features.plugins=false" in on
    finally:
        adapter.server.server_close()
        adapter.client.close()


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
