"""The agent's prompt and the import skill must agree: a sheet the owner sends is imported in the same turn, then reported.
They once disagreed (the prompt said import, the skill said wait for the owner to agree), so about 30% of first files were not
imported until the owner replied yes."""
from pathlib import Path

from src.agent import prompt

SKILL = (Path(__file__).resolve().parents[1] / "skills" / "tally-and-file-import" / "SKILL.md").read_text(encoding="utf-8")


def test_the_skill_does_not_tell_the_agent_to_wait_before_importing():
    assert "once they agree" not in SKILL and "before importing" not in SKILL.lower().replace("ask before importing", "")
    assert "without waiting for the owner to agree" in SKILL


def test_the_prompt_says_import_first_then_report():
    text = prompt.BASE
    assert "Do not ask for confirmation first" in text and "no WhatsApp number (by name)" in text
