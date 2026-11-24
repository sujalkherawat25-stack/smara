"""Document tools: Word, Excel, PowerPoint, PDF, posters and reading, with safe paths and checked formulas."""
import io
import json
from pathlib import Path

import pytest

from smara import docs_build as build
from smara import docs_html
from smara import native_docs as nd


@pytest.fixture
def ws(tmp_path):
    return str(tmp_path)


def no_browser(monkeypatch):
    def refuse(*args, **kwargs):
        raise docs_html.BrowserMissing("none")
    monkeypatch.setattr(docs_html, "print_pdf", refuse)


ROWS = [["Anil", 12, 184000], ["Sunita", 9, 142500], ["Ravi", 15, 231000], ["Meena", 7, 98000]]


def test_indian_digit_grouping_and_rupee_sign():
    assert build.inr(184000) == "1,84,000"
    assert build.inr(12345678.5, 2) == "1,23,45,678.50"
    assert build.inr(999) == "999" and build.inr(1000) == "1,000" and build.inr(100000) == "1,00,000"
    assert build.inr(-250000, symbol=True) == "-₹2,50,000"
    assert build.display(655500, "inr") == "6,55,500" and build.display(0.256, "percent") == "25.6%" and build.display(12.0) == "12"


def test_text_colour_is_chosen_for_contrast():
    for color in ("#0E5C63", "#E2673B", "#F2A900", "#FFFFFF", "#222831"):
        assert build.contrast(build.text_on(color), color) >= 4.5, color


def test_excel_totals_are_real_formulas_and_the_tool_reports_what_they_come_to(ws):
    out = nd.call("doc_make_xlsx", {"path": "premium", "sheets": [{"name": "Premium", "header": ["Agent", "Policies", "Premium"], "rows": ROWS,
                                                                  "totals": {"columns": ["B", "C"]}, "formats": {"C": "inr"}}]}, ws)
    assert out["path"] == "premium.xlsx"
    sheet = out["sheets"][0]
    assert sheet["computed"] == {"B6": 43, "C6": 655500} and sheet["unverified"] == []
    from openpyxl import load_workbook
    book = load_workbook(Path(ws) / "premium.xlsx")
    page = book["Premium"]
    assert page["C6"].value == "=SUM(C2:C5)" and page["A1"].font.bold and page.freeze_panes == "A2"
    assert "##\\,##\\,##0" in page["C2"].number_format


def test_the_formula_checker_handles_common_formulas_and_refuses_what_it_cannot_check():
    grid = build.Grid({"A1": 10, "A2": 20, "A3": "=SUM(A1:A2)", "B1": "=A3*2", "B2": "=ROUND(A3/7,2)", "B3": "=A1/0", "C1": "=C2", "C2": "=C1",
                       "D1": "=Sheet2!A1+1", "D2": "=MAX(A1:A2)-MIN(A1:A2)", "D3": "=AVERAGE(A1:A2)", "D4": "=2^3+ABS(-4)"})
    assert grid.value("A3") == 30 and grid.value("B1") == 60 and grid.value("B2") == 4.29
    assert grid.value("D2") == 10 and grid.value("D3") == 15 and grid.value("D4") == 12
    for bad in ("B3", "C1", "D1"):
        with pytest.raises(build.Unverifiable):
            grid.value(bad)


def test_unverifiable_formulas_are_listed_not_hidden(ws):
    out = nd.call("doc_make_xlsx", {"path": "x", "sheets": [{"name": "S", "header": ["A"], "rows": [[1], ["=Other!A1"], ["=A2/0"]]}]}, ws)
    notes = out["sheets"][0]["unverified"]
    assert len(notes) == 2 and any("other sheets" in n for n in notes) and any("division by zero" in n for n in notes)


def test_word_document_round_trips_through_doc_read(ws):
    blocks = [{"type": "heading", "text": "Agent-wise", "level": 1}, {"type": "paragraph", "text": "Top agent: Ravi"},
              {"type": "table", "header": ["Agent", "Premium"], "rows": [["Anil", 184000], ["Total", 184000]], "total_row": True, "formats": [None, "inr"]},
              {"type": "bullets", "items": ["बीमा सप्ताह", "Follow up"]}]
    out = nd.call("doc_make_docx", {"path": "reports/oct", "title": "Branch report", "blocks": blocks, "footer_text": "Confidential"}, ws)
    assert out["path"] == "reports/oct.docx" and (Path(ws) / "reports" / "oct.docx").is_file()
    text = nd.call("doc_read", {"path": "reports/oct.docx"}, ws)["text"]
    assert "Branch report" in text and "# Agent-wise" in text and "Anil | 1,84,000" in text and "बीमा सप्ताह" in text


def test_powerpoint_round_trips_with_notes_and_a_table(ws):
    out = nd.call("doc_make_pptx", {"path": "deck", "slides": [{"title": "Review", "subtitle": "October"},
        {"title": "Highlights", "bullets": ["43 policies", "Ravi is top"], "notes": "thank the team"},
        {"title": "Numbers", "table": {"header": ["Agent", "Policies"], "rows": [["Anil", 12]]}}]}, ws)
    assert out["slides"] == 3
    text = nd.call("doc_read", {"path": "deck.pptx"}, ws)["text"]
    assert "== Slide 2" in text and "43 policies" in text and "Notes: thank the team" in text and "Anil | 12" in text


def test_pdf_without_a_browser_works_for_plain_text_and_refuses_hindi_it_cannot_shape(ws, monkeypatch):
    no_browser(monkeypatch)
    out = nd.call("doc_make_pdf", {"path": "r", "title": "Report", "blocks": [{"type": "paragraph", "text": "Total Rs 6,55,500"}]}, ws)
    assert out["engine"] == "plain" and out["pages"] == 1
    assert "Total Rs 6,55,500" in nd.call("doc_read", {"path": "r.pdf"}, ws)["text"]
    with pytest.raises(nd.DocsError, match="Edge or Google Chrome"):
        nd.call("doc_make_pdf", {"path": "h", "blocks": [{"type": "paragraph", "text": "बीमा सप्ताह"}]}, ws)


def test_poster_makes_a_pdf_and_a_shareable_picture(ws, monkeypatch):
    no_browser(monkeypatch)
    out = nd.call("doc_make_poster", {"path": "camp", "title": "Health Camp", "tagline": "3 to 9 November", "highlight": "Free",
                                      "details": ["Nehru Market", "10 am to 5 pm"], "call_to_action": "Call 98765 43210", "size": "square"}, ws)
    assert out["path"] == "camp.pdf" and out["picture"] == "camp.png" and out["layout_warnings"] == "none"
    from PIL import Image
    with Image.open(Path(ws) / "camp.png") as picture:
        assert picture.width == 1080 and picture.height == 1080


def test_poster_needs_a_title_and_clamps_unknown_sizes(ws, monkeypatch):
    no_browser(monkeypatch)
    with pytest.raises(ValueError, match="title"):
        nd.call("doc_make_poster", {"path": "p", "title": ""}, ws)
    assert nd.call("doc_make_poster", {"path": "q", "title": "Sale", "size": "billboard"}, ws)["size"] == "A4"


def test_the_browser_prints_a_poster_with_correct_hindi_when_one_is_installed(ws):
    from smara.smara_bridge import find_browser
    if not find_browser("auto"):
        pytest.skip("no Edge or Chrome on this machine")
    out = nd.call("doc_make_poster", {"path": "hindi", "title": "जीवन सुरक्षा सप्ताह", "details": ["निःशुल्क स्वास्थ्य शिविर"], "call_to_action": "Call 98765 43210"}, ws)
    assert out["engine"] == "browser" and out["layout_warnings"] == "none"
    assert "निःशुल्क" in nd.call("doc_read", {"path": "hindi.pdf"}, ws)["text"]


def test_files_are_never_overwritten(ws):
    first = nd.call("doc_make_docx", {"path": "note", "blocks": [{"type": "paragraph", "text": "one"}]}, ws)
    second = nd.call("doc_make_docx", {"path": "note.docx", "blocks": [{"type": "paragraph", "text": "two"}]}, ws)
    third = nd.call("doc_make_docx", {"path": "note", "blocks": [{"type": "paragraph", "text": "three"}]}, ws)
    assert [first["path"], second["path"], third["path"]] == ["note.docx", "note (2).docx", "note (3).docx"]
    assert "one" in nd.call("doc_read", {"path": "note.docx"}, ws)["text"]


@pytest.mark.parametrize("bad", ["../outside", "/abs/x", "C:\\Windows\\x", "a/../../b", ".git/config", ".smara/x", "", "***"])
def test_paths_stay_inside_the_project_and_out_of_state_folders(ws, bad):
    with pytest.raises(nd.DocsError):
        nd.call("doc_make_docx", {"path": bad, "blocks": [{"type": "paragraph", "text": "x"}]}, ws)


def test_pictures_come_only_from_the_project_and_must_be_images(ws, tmp_path):
    from PIL import Image
    Image.new("RGB", (40, 20), "red").save(tmp_path / "logo.png")
    (tmp_path / "notes.txt").write_text("hi")
    blocks = lambda path: [{"type": "image", "path": path}]
    assert nd.call("doc_make_docx", {"path": "ok", "blocks": blocks("logo.png")}, ws)["bytes"] > 0
    for bad in ("notes.txt", "../logo.png", "missing.png"):
        with pytest.raises(nd.DocsError):
            nd.call("doc_make_docx", {"path": "bad", "blocks": blocks(bad)}, ws)


def test_bad_content_is_refused_with_a_plain_reason(ws):
    for blocks, word in (([{"type": "script", "text": "x"}], "Unknown block"), ([], "content blocks"), (["x"], "object"),
                         ([{"type": "table", "rows": [list(range(60))]}], "at most")):
        with pytest.raises(ValueError, match=word):
            nd.call("doc_make_docx", {"path": "bad", "blocks": blocks}, ws)
    with pytest.raises(nd.DocsError, match="Unexpected arguments"):
        nd.call("doc_make_docx", {"path": "x", "blocks": [{"type": "spacer"}], "macro": "1"}, ws)


def test_reading_refuses_other_kinds_and_pages_long_files(ws):
    (Path(ws) / "a.txt").write_text("x")
    with pytest.raises(nd.DocsError, match="docx, .xlsx"):
        nd.call("doc_read", {"path": "a.txt"}, ws)
    rows = [[f"row {i}", i] for i in range(1000)]
    nd.call("doc_make_xlsx", {"path": "big", "sheets": [{"name": "S", "header": ["a", "b"], "rows": rows}]}, ws)
    page = nd.call("doc_read", {"path": "big.xlsx"}, ws)
    assert page["total_chars"] > 0 and "== Sheet S" in page["text"] and "row 198" in page["text"] and "row 250" not in page["text"] and "first 200 of 1001 rows" in page["text"]


def test_reading_shows_formulas_with_their_saved_values(ws, tmp_path):
    from openpyxl import Workbook
    book = Workbook()
    sheet = book.active
    sheet.append([1, 2])
    sheet["C1"] = "=A1+B1"
    book.save(tmp_path / "f.xlsx")
    assert "=A1+B1" in nd.call("doc_read", {"path": "f.xlsx"}, ws)["text"]


def test_the_server_speaks_mcp_and_reports_errors_plainly(ws, monkeypatch, capsys):
    lines = [json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"}),
             json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
             json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "doc_make_docx", "arguments": {"path": "../x", "blocks": [{"type": "spacer"}]}}}),
             json.dumps({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "doc_make_docx", "arguments": {"path": "ok", "blocks": [{"type": "spacer"}]}}})]
    monkeypatch.setattr("sys.stdin", io.StringIO("\n".join(lines) + "\n"))
    assert nd.main(["--workspace", ws]) == 0
    replies = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert {t["name"] for t in replies[1]["result"]["tools"]} == {"doc_make_docx", "doc_make_pdf", "doc_make_xlsx", "doc_make_pptx", "doc_make_poster", "doc_read"}
    assert replies[2]["result"]["isError"] and "inside the project" in replies[2]["result"]["content"][0]["text"]
    assert not replies[3]["result"]["isError"] and (Path(ws) / "ok.docx").is_file()


def test_every_chat_offers_the_document_tools_without_asking_each_time():
    source = Path(nd.__file__).with_name("native_runtime.py").read_text(encoding="utf-8")
    assert '"mcp_servers.smara_docs.enabled": True' in source and 'docs_approval(entry["name"])' in source
    assert all(nd.approval_mode(t["name"]) == "approve" for t in nd.TOOLS)
    from smara.native_prompt import BASE_PROMPT
    assert "doc_make_docx" in BASE_PROMPT and "doc_read" in BASE_PROMPT


def test_a_file_we_made_can_be_replaced_until_the_owner_changes_it(ws):
    first = nd.call("doc_make_docx", {"path": "r", "blocks": [{"type": "paragraph", "text": "v1"}]}, ws)
    again = nd.call("doc_make_docx", {"path": "r", "replace": True, "blocks": [{"type": "paragraph", "text": "v2"}]}, ws)
    assert first["path"] == again["path"] == "r.docx" and again["replaced"] is True
    assert "v2" in nd.call("doc_read", {"path": "r.docx"}, ws)["text"] and not list(Path(ws).glob("*.new*"))
    from docx import Document
    owner = Document(str(Path(ws) / "r.docx"))
    owner.add_paragraph("the owner's own edit")
    owner.save(str(Path(ws) / "r.docx"))
    safe = nd.call("doc_make_docx", {"path": "r", "replace": True, "blocks": [{"type": "paragraph", "text": "v3"}]}, ws)
    assert safe["path"] == "r (2).docx" and "replaced" not in safe
    assert "the owner's own edit" in nd.call("doc_read", {"path": "r.docx"}, ws)["text"]


def test_replace_never_touches_a_file_the_owner_made_and_a_failed_build_keeps_the_old_file(ws, tmp_path):
    from docx import Document
    Document().save(str(tmp_path / "mine.docx"))
    out = nd.call("doc_make_docx", {"path": "mine", "replace": True, "blocks": [{"type": "paragraph", "text": "x"}]}, ws)
    assert out["path"] == "mine (2).docx"
    nd.call("doc_make_docx", {"path": "keep", "blocks": [{"type": "paragraph", "text": "good"}]}, ws)
    with pytest.raises(ValueError):
        nd.call("doc_make_docx", {"path": "keep", "replace": True, "blocks": [{"type": "bogus"}]}, ws)
    assert "good" in nd.call("doc_read", {"path": "keep.docx"}, ws)["text"] and not list(Path(ws).glob("*.new*"))


def test_poster_and_picture_replace_together(ws, monkeypatch):
    no_browser(monkeypatch)
    one = nd.call("doc_make_poster", {"path": "p", "title": "Sale"}, ws)
    two = nd.call("doc_make_poster", {"path": "p", "title": "Big Sale", "replace": True}, ws)
    assert one["path"] == two["path"] == "p.pdf" and two["picture"] == "p.png" and two["replaced"] is True
    assert sorted(f.name for f in Path(ws).iterdir() if f.is_file()) == ["p.pdf", "p.png"]


def test_the_number_format_keeps_whole_numbers_whole():
    assert build.display(12, "number") == "12" and build.display(1234.5, "number") == "1,234.50" and build.display(1234567, "number") == "1,234,567"
