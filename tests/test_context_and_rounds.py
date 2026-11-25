"""What a long task sends to the model, when a finished task needs no second round, and which tools a phone task loads."""
import hashlib
import json

from smara import docs_build
from smara.native_goal_runtime import PHONE_MARK, unneeded_tool_options
from smara.native_outcomes import review, validate_acceptance
from smara.native_provider import OLD_RESULT_CHARS, RECENT_TOOL_RESULTS, translate_request

PICTURE = "data:image/png;base64," + "A" * 50_000


def history(outputs):
    items = [{"type": "message", "role": "user", "content": "do it"}]
    for number, output in enumerate(outputs):
        items.append({"type": "function_call", "name": "tool", "arguments": json.dumps({"n": number}), "call_id": f"c{number}"})
        items.append({"type": "function_call_output", "call_id": f"c{number}", "output": output})
    return {"input": items, "tools": [{"type": "function", "name": "tool", "parameters": {"type": "object", "properties": {}}}]}


def picture(text="opened"):
    return [{"type": "input_text", "text": text}, {"type": "input_image", "image_url": PICTURE}]


def tool_texts(payload):
    return [m["content"] for m in payload["messages"] if m["role"] == "tool"]


def test_a_text_only_model_gets_a_note_instead_of_a_picture():
    payload, _ = translate_request(history([picture()]), "glm5.3", sarvam=True)
    sent = json.dumps(payload)
    assert "AAAAAAAAAA" not in sent and len(sent) < 3000
    assert "has NOT seen the picture" in tool_texts(payload)[0] and "opened" in tool_texts(payload)[0]


def test_a_model_that_sees_pictures_gets_them_after_all_the_tool_results():
    request = history([picture("one")])
    request["input"] += [{"type": "function_call", "name": "tool", "arguments": "{}", "call_id": "x"},
                         {"type": "function_call", "name": "tool", "arguments": "{}", "call_id": "y"},
                         {"type": "function_call_output", "call_id": "x", "output": picture("two")},
                         {"type": "function_call_output", "call_id": "y", "output": "plain"}]
    payload, _ = translate_request(request, "gemma4", sarvam=True)
    roles = [m["role"] for m in payload["messages"]]
    assert roles == ["user", "assistant", "tool", "user", "assistant", "tool", "tool", "user"]
    assert all("AAAAAAAAAA" not in text for text in tool_texts(payload))
    assert payload["messages"][-1]["content"][1] == {"type": "image_url", "image_url": {"url": PICTURE}}


def test_an_old_picture_is_not_sent_again():
    payload, _ = translate_request(history([picture()] + ["ok"] * RECENT_TOOL_RESULTS), "gemma4", sarvam=True)
    assert "AAAAAAAAAA" not in json.dumps(payload)


def test_only_big_old_results_are_shortened():
    big = "S" * 5000 + "M" * 30_000 + "E" * 1500
    payload, _ = translate_request(history([big, "short old one"] + [big] * RECENT_TOOL_RESULTS), "glm5.3", sarvam=True)
    texts = tool_texts(payload)
    assert len(texts[0]) < OLD_RESULT_CHARS and texts[0].startswith("S" * 3000) and texts[0].endswith("E" * 1000)
    assert "left out to keep the task small" in texts[0] and texts[1] == "short old one"
    assert texts[2:] == [big] * RECENT_TOOL_RESULTS
    calls = [c for m in payload["messages"] for c in m.get("tool_calls", [])]
    assert [json.loads(c["function"]["arguments"])["n"] for c in calls] == list(range(RECENT_TOOL_RESULTS + 2))


def make_pdf(path, blocks):
    docs_build.build_pdf_report(path, {"blocks": blocks, "title": "Dues summary"}, lambda name: None)


def outcome(tmp_path, name):
    return review({"workspace": str(tmp_path), "task_kind": "general"}, {"outputs": [name]}, {"turns": [{"items": []}]})


def test_a_good_pdf_passes_without_the_model_having_to_inspect_it(tmp_path):
    make_pdf(tmp_path / "dues.pdf", [{"type": "table", "header": ["Party", "Amount"], "rows": [["Ravi", 1], ["Total", 1]]}])
    result = outcome(tmp_path, "dues.pdf")
    assert result["passed"], result["checks"]
    validate_acceptance({"spec": {"workspace": str(tmp_path), "task_kind": "general"}, "checkpoint": {"outcome": result}})


def test_a_pdf_with_a_layout_problem_still_fails_and_says_what_is_wrong(tmp_path, monkeypatch):
    from smara import native_pdf
    make_pdf(tmp_path / "faint.pdf", [{"type": "paragraph", "text": "hello"}])
    real = native_pdf.inspect_pdf

    def with_warning(raw, **options):
        found = real(raw, **options)
        found["pages"][0]["issues"] = ["low_contrast_text"]
        return found

    monkeypatch.setattr(native_pdf, "inspect_pdf", with_warning)
    result = outcome(tmp_path, "faint.pdf")
    check = next(c for c in result["checks"] if c["name"] == "PDF layout preflight")
    assert not result["passed"] and "low_contrast_text" in check["detail"]


def test_something_that_is_not_a_pdf_stays_failed(tmp_path):
    (tmp_path / "broken.pdf").write_bytes(b"not a pdf")
    assert not outcome(tmp_path, "broken.pdf")["passed"]


def phone(words, **extra):
    return {"objective": f"{words}\n\n{PHONE_MARK} Do it on this PC by yourself. Every morning is mentioned in these "
                         "standing instructions only to prove they are not read as the owner's words.)", **extra}


def test_an_ordinary_phone_task_leaves_the_team_tools_out_but_keeps_skills():
    options = unneeded_tool_options(phone("How much free space does my laptop have?"))
    assert options[0] == "-c" and options[1].startswith("mcp_servers.smara_team.disabled_tools=")
    off = json.loads(options[1].split("=", 1)[1])
    assert "team_delegate" in off and "team_routine" in off and not any(name.startswith("skill_") for name in off)


def test_team_tools_stay_for_team_or_schedule_requests_and_for_other_tasks():
    for words in ("Hire an accountant for me", "Ask my team to check the invoices", "Every morning, list my new downloads",
                  "Roz subah dues bhejo", "हर दिन रिपोर्ट बनाओ", "Delegate this to Kiran"):
        assert unneeded_tool_options(phone(words)) == [], words
    assert unneeded_tool_options({"objective": "Reconcile the books"}) == []                 # started on the PC
    assert unneeded_tool_options(phone("Reconcile the books", agent_id="kiran")) == []      # an employee's own job


PDF_REQUEST = {"input": "make a pdf", "tools": [{"type": "function", "name": "pdf_inspect", "parameters": {"type": "object", "properties": {}}}]}


def system_text(model, sarvam):
    payload, _ = translate_request(PDF_REQUEST, model, sarvam=sarvam)
    return " ".join(m["content"] for m in payload["messages"] if m["role"] == "system")


def test_a_text_only_model_is_not_told_to_make_page_pictures():
    reading = system_text("glm5.3", True)
    assert "preview_command_argv" not in reading and "render page PNGs" not in reading
    assert "Do not make page pictures" in reading and "doc_read" in reading
    for model, sarvam in (("gemma4", True), ("other-model", False)):
        assert "preview_command_argv" in system_text(model, sarvam)


def test_the_pdf_checker_gives_the_picture_command_only_to_a_model_that_sees(tmp_path, monkeypatch):
    from smara.native_pdf import inspect_workspace_pdf
    make_pdf(tmp_path / "dues.pdf", [{"type": "paragraph", "text": "hello"}])
    monkeypatch.setenv("SMARA_MODEL_SEES_PICTURES", "0")
    found = inspect_workspace_pdf(str(tmp_path), "dues.pdf")
    assert "preview_command_argv" not in found and "do not make page pictures" in found["limitations"]
    assert "Review page images" not in found["limitations"]
    monkeypatch.setenv("SMARA_MODEL_SEES_PICTURES", "1")
    assert "preview_command_argv" in inspect_workspace_pdf(str(tmp_path), "dues.pdf")


def test_the_tool_description_does_not_ask_for_a_visual_review():
    from smara.native_tools import TOOLS
    assert "visually review" not in next(t for t in TOOLS if t["name"] == "pdf_inspect")["description"]
