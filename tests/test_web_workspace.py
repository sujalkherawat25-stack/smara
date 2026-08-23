from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_native_web_workspace_includes_chat_and_control_surfaces():
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

    for element_id in (
        'id="chat"',
        'id="tasks"',
        'id="research"',
        'id="approvals"',
        'id="integrations"',
        'id="devices"',
        'id="memory"',
    ):
        assert element_id in html
    assert "/v1/chat/stream" in script
    assert "api('/v1/research')" in script
    assert "/v1/conversations" in script
    assert "/v1/cli/devices" in script
    assert "/v1/executors/" in script
    assert "/v1/tasks/${id}/approval" in script


def test_web_does_not_render_chat_tokens_as_html():
    script = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert "answer.textContent += payload.text" in script
    assert "answer.innerHTML" not in script
