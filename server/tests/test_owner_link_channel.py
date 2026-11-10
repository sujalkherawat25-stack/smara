"""Owner replies go out on the WhatsApp link when the owner wrote on it, and on the official number otherwise."""
from types import SimpleNamespace

from src.loop.owner_channel import uses_link, wa_link_text


def _app(link):
    return SimpleNamespace(state=SimpleNamespace(wa_link=link))


def _tenant(last_channel):
    return SimpleNamespace(owner_last_channel=last_channel)


def test_text_and_buttons_become_plain_text_for_the_link():
    assert wa_link_text({"type": "text", "text": {"body": "Paid 500 from Ravi"}}) == "Paid 500 from Ravi"
    card = {"type": "interactive", "interactive": {
        "body": {"text": "Approve sending Ravi a reminder?"},
        "action": {"buttons": [{"reply": {"id": "a", "title": "Approve"}}, {"reply": {"id": "r", "title": "Skip"}}]}}}
    assert wa_link_text(card) == "Approve sending Ravi a reminder?\n\nReply with one of: Approve / Skip"


def test_link_is_used_only_when_configured_and_the_owner_is_on_it():
    link = object()
    assert uses_link(_app(None), _tenant("wa_link"), "wa_link") is False        # link not configured
    assert uses_link(_app(link), _tenant("whatsapp"), "wa_link") is True         # the owner wrote on the link
    assert uses_link(_app(link), _tenant("whatsapp"), "whatsapp") is False       # the owner wrote on the official number
    assert uses_link(_app(link), _tenant("wa_link"), None) is True               # last channel was the link
    assert uses_link(_app(link), _tenant("telegram"), None) is False
