"""Smara's own short base prompt replaces the generic Codex CLI prompt, and long chats compact early to stay fast."""
from pathlib import Path

from smara import native_prompt
from smara.native_runtime import FAST_CONTEXT_TOKENS


def test_base_prompt_is_short_smara_and_keeps_the_safety_rules(tmp_path):
    text = native_prompt.BASE_PROMPT
    assert len(text) < 4000                                    # was 21 KB (~5k tokens) on every message
    assert "You are Smara" in text and "Codex CLI" not in text
    for rule in ("data, never instructions", "never moves money", "OTP", "apply_patch"):
        assert rule in text
    path = native_prompt.write_base_prompt(tmp_path)
    assert path.read_text(encoding="utf-8") == text
    assert native_prompt.write_base_prompt(tmp_path) == path   # rewriting is idempotent


def test_long_chats_compact_well_before_they_get_slow():
    assert FAST_CONTEXT_TOKENS <= 32_000
    source = Path(native_prompt.__file__).with_name("native_runtime.py").read_text(encoding="utf-8")
    assert 'settings["model_instructions_file"] = str(write_base_prompt(home))' in source
    assert "min(int(window * .8), FAST_CONTEXT_TOKENS)" in source
