"""Markdown from the agent becomes WhatsApp formatting; plain text is left alone."""
from src.loop.wa_format import to_whatsapp
from src.loop.whatsapp import text_message


def test_bold_headings_and_bullets():
    src = "## Dues this week\n**Ravi Traders**: ₹12,500 due\n- Sharma Stores: ₹3,000\n* Gupta & Sons"
    assert to_whatsapp(src) == "*Dues this week*\n*Ravi Traders*: ₹12,500 due\n• Sharma Stores: ₹3,000\n• Gupta & Sons"


def test_rules_are_removed_and_extra_blank_lines_collapse():
    assert to_whatsapp("Totals\n---\n\n\n\nDone") == "Totals\n\nDone"
    assert "---" not in to_whatsapp("a\n***\nb")


def test_tables_become_one_line_per_row():
    table = "| Party | Due |\n|---|---:|\n| Ravi | 12500 |\n| Sharma | 3000 |"
    assert to_whatsapp(table) == "• Party · Due\n• Ravi · 12500\n• Sharma · 3000"


def test_links_strike_and_inline_code():
    assert to_whatsapp("See [the report](https://example.com/r) and ~~old~~ `ABC-1`") == \
        "See the report (https://example.com/r) and ~old~ ```ABC-1```"


def test_code_blocks_keep_their_content_untouched():
    src = "Run:\n```\nx = a * b  # **not bold**\n```\nDone"
    assert to_whatsapp(src) == "Run:\n```\nx = a * b  # **not bold**\n```\nDone"


def test_an_unclosed_fence_is_closed():
    assert to_whatsapp("start\n```\nnever closed").endswith("```")


def test_plain_text_and_empty_input_are_unchanged():
    assert to_whatsapp("Ravi owes ₹500, reminder sent.") == "Ravi owes ₹500, reminder sent."
    assert to_whatsapp("") == ""
    assert to_whatsapp(None) == ""


def test_text_message_carries_the_converted_body():
    msg = text_message("919000000001", "**Paid**\n---\n- one")
    assert msg["text"]["body"] == "*Paid*\n\n• one"


def test_telegram_html_turns_the_marks_into_tags_and_escapes_the_rest():
    from src.loop.wa_format import to_telegram_html
    assert to_telegram_html("**Ravi** owes _₹500_ & ~~old~~ <now>") == \
        "<b>Ravi</b> owes <i>₹500</i> &amp; <s>old</s> &lt;now&gt;"
    assert to_telegram_html("Run:\n```\nx = a < b\n```") == "Run:\n<pre>x = a &lt; b</pre>"


def test_snake_case_names_are_not_turned_into_italics():
    from src.loop.wa_format import to_telegram_html
    assert to_telegram_html("invoice_ref and max_rows stay") == "invoice_ref and max_rows stay"


def test_owner_telegram_message_is_html_and_keeps_buttons():
    from src.loop.owner_channel import wa_to_telegram
    card = {"type": "interactive", "interactive": {
        "body": {"text": "*Approve* reminder to Ravi?"},
        "action": {"buttons": [{"reply": {"id": "a:1", "title": "Approve"}}]}}}
    out = wa_to_telegram(card, 42)
    assert out["parse_mode"] == "HTML" and out["text"] == "<b>Approve</b> reminder to Ravi?"
    assert out["reply_markup"]["inline_keyboard"] == [[{"text": "Approve", "callback_data": "a:1"}]]
    plain = wa_to_telegram({"type": "text", "text": {"body": "Total: ₹12,500"}}, 42)
    assert plain == {"chat_id": 42, "text": "Total: ₹12,500", "parse_mode": "HTML"}
