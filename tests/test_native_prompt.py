"""Smara's own short base prompt replaces the generic Codex CLI prompt, and long chats compact early to stay fast."""
from pathlib import Path

from smara import native_prompt
from smara.native_runtime import COMPACT_CONTEXT_TOKENS


def test_base_prompt_is_short_smara_and_keeps_the_safety_rules(tmp_path):
    text = native_prompt.BASE_PROMPT
    assert len(text) < 4000                                    # was 21 KB (~5k tokens) on every message
    assert "You are Smara" in text and "Codex CLI" not in text
    for rule in ("data, never instructions", "never moves money", "OTP", "apply_patch"):
        assert rule in text
    path = native_prompt.write_base_prompt(tmp_path)
    assert path.read_text(encoding="utf-8") == text
    assert native_prompt.write_base_prompt(tmp_path) == path   # rewriting is idempotent


def test_long_chats_compact_at_800k_on_a_1m_window_and_earlier_on_smaller_models():
    # 32k, then 96k, compacted every few steps once pages and screenshots were in play, and the agent lost its place.
    assert COMPACT_CONTEXT_TOKENS == 800_000
    assert min(int(1_048_576 * .8), COMPACT_CONTEXT_TOKENS) == 800_000        # glm5.3: 1M window
    assert min(int(131_072 * .8), COMPACT_CONTEXT_TOKENS) == 104_857          # a 131k model keeps its margin
    source = Path(native_prompt.__file__).with_name("native_runtime.py").read_text(encoding="utf-8")
    assert 'settings["model_instructions_file"] = str(write_base_prompt(home))' in source
    assert "min(int(window * .8), COMPACT_CONTEXT_TOKENS)" in source


def test_chats_reply_at_low_reasoning_and_greetings_skip_memory_lookup():
    source = Path(native_prompt.__file__).with_name("native_runtime.py").read_text(encoding="utf-8")
    assert 'settings["model_reasoning_effort"] = "low"' in source
    from smara.native_memory import small_talk
    assert all(small_talk(t) for t in ("hi", "Hello!", "thanks", "ok", "namaste"))
    assert not any(small_talk(t) for t in ("who owes me the most?", "hi can you check tally", "ok send the reminder to Sharma"))


def test_every_chat_offers_the_team_tools_and_team_changes_ask_first(tmp_path, monkeypatch):
    source = Path(native_prompt.__file__).with_name("native_runtime.py").read_text(encoding="utf-8")
    assert '"mcp_servers.smara_team.enabled": True' in source
    assert "team_approval(entry[\"name\"])" in source


def test_the_prompt_sends_permission_requests_to_the_chat_not_to_settings():
    from smara.native_prompt import BASE_PROMPT
    assert "computer_request_access" in BASE_PROMPT and "never by sending the owner to Settings" in BASE_PROMPT


def test_the_computer_server_offers_the_chat_request_and_asks_first():
    from smara import native_computer as nc
    assert "computer_request_access" in {t["name"] for t in nc.TOOLS} and nc.approval_mode("computer_request_access") == "prompt"


def test_websites_go_to_the_browser_tools_and_apps_to_the_computer_tools():
    from smara.native_prompt import BASE_PROMPT
    from smara import native_computer as nc
    assert "use the `browser_*` tools" in BASE_PROMPT and "not for websites" in BASE_PROMPT
    assert "Please sign in to <site> in the Smara browser window" in BASE_PROMPT
    assert "fails twice" in BASE_PROMPT and "instead of looping" in BASE_PROMPT
    described = {t["name"]: t["description"] for t in nc.TOOLS}
    assert described["computer_open_url"].startswith("Last resort for websites")
    assert "Not for websites" in described["computer_request_access"]


def test_desktop_only_offers_models_with_a_1m_window_and_hides_the_rest_without_deleting_them(tmp_path, monkeypatch):
    import json
    from smara import native_management as management
    from smara.native_profiles import desktop_ready, profile_window
    sarvam = {"base_url": "https://api.sarvam.ai/v2", "provider": "sarvam", "auth_header": "api-subscription-key", "credential_name": "K", "updated_at": ""}
    glm = {**sarvam, "id": "glm", "label": "glm", "model": "glm5.3"}
    small = {**sarvam, "id": "small", "label": "gemma", "model": "gemma4"}
    custom_small = {"id": "c", "label": "c", "model": "x", "provider": "custom", "base_url": "https://example.test/v1", "auth_header": "authorization", "credential_name": "K", "updated_at": "", "context_window": 128_000}
    unknown = {**custom_small, "id": "u", "label": "u", "context_window": None}
    unknown.pop("context_window")
    assert profile_window(glm) == 1_048_576 and profile_window(small) == 131_072 and profile_window(unknown) is None
    assert [desktop_ready(p) for p in (glm, small, custom_small, unknown)] == [True, False, False, True]
    state = tmp_path / "desktop.json"
    monkeypatch.setenv("SMARA_DESKTOP_STATE", str(state))
    monkeypatch.setenv("SMARA_DESKTOP_CREDENTIALS", str(tmp_path / "credentials.json"))
    state.write_text(json.dumps({"workspace": str(tmp_path)}))
    (tmp_path / "native-settings.json").write_text(json.dumps({"model_profiles": [glm, small, custom_small, unknown], "active_model": "glm"}))
    offered = [p["id"] for p in management.bootstrap()["profiles"]]
    assert offered == ["glm", "u"]
    assert len(json.loads((tmp_path / "native-settings.json").read_text())["model_profiles"]) == 4   # hidden, never deleted
