"""Real local renderer fixtures. These are not live-model quality evaluations."""
import asyncio
from io import BytesIO
import json

import pytest
from reportlab.lib.colors import HexColor
from reportlab.pdfgen.canvas import Canvas

from smara.native_documents import DocumentError
from smara.native_pdf import inspect_pdf, inspect_workspace_pdf
from smara.native_tools import NativeTools, serve


def fixture_pdf(*, dark=False, sparse=False, pages=1, outside=False):
    output = BytesIO()
    canvas = Canvas(output, pagesize=(600, 800))
    for page in range(pages):
        if dark:
            canvas.setFillColor(HexColor("#0B1220"))
            canvas.rect(0, 0, 600, 800, fill=1, stroke=0)
        canvas.setFillColor(HexColor("#172033"))
        canvas.setFont("Helvetica", 12)
        for line in range(2 if sparse and page else 26):
            canvas.drawString(55 if not outside else -25, 735 - line * 22, "Architecture review: source, tools, sessions and verification.")
        canvas.showPage()
    canvas.save()
    return output.getvalue()


def test_detects_original_dark_text_on_cover_background_without_browser():
    bad = inspect_pdf(fixture_pdf(dark=True))
    good = inspect_pdf(fixture_pdf())
    assert "low_contrast_text" in bad["pages"][0]["issues"]
    assert good["pages"][0]["issues"] == []
    assert good["pages"][0]["rendered"] is True
    assert good["visual_review_completed"] is False


def test_flags_orphan_page_and_text_outside_page():
    sparse = inspect_pdf(fixture_pdf(pages=2, sparse=True))
    assert "sparse_page_check_pagination" in sparse["pages"][1]["issues"]
    outside = inspect_pdf(fixture_pdf(outside=True))
    assert "text_outside_page" in outside["pages"][0]["issues"]


def test_pdf_ranges_are_bounded_and_not_silently_truncated():
    raw = fixture_pdf(pages=6)
    first = inspect_pdf(raw)
    second = inspect_pdf(raw, start_page=first["next_page"])
    assert [p["page"] for p in first["pages"]] == [1, 2, 3, 4]
    assert [p["page"] for p in second["pages"]] == [5, 6]
    assert second["next_page"] is None
    assert first["content_sha256"] == second["content_sha256"]


@pytest.mark.parametrize("start,count", [(0, 1), (True, 1), (1, 5), (1, False), (7, 1)])
def test_reject_invalid_or_nonexistent_pages(start, count):
    with pytest.raises(DocumentError):
        inspect_pdf(fixture_pdf(), start_page=start, max_pages=count)


def test_bad_pdf_does_not_return_parser_content():
    with pytest.raises(DocumentError, match="no visual verification") as error:
        inspect_pdf(b"%PDF-1.4\nprivate-fixture-content")
    assert "private-fixture" not in str(error.value)


def test_mcp_inspection_is_local_read_only_and_current_after_replacement(tmp_path, monkeypatch):
    pdf = tmp_path / "report.pdf"
    pdf.write_bytes(fixture_pdf(dark=True))
    def network_forbidden(*args, **kwargs):
        raise AssertionError("PDF preflight must not upload or resolve keys")
    monkeypatch.setattr("smara.native_documents.sarvam_key", network_forbidden)
    tools = NativeTools(tmp_path, context_only=True)
    first = asyncio.run(tools.call("pdf_inspect", {"path": "report.pdf"}))
    pdf.write_bytes(fixture_pdf())
    second = asyncio.run(tools.call("pdf_inspect", {"path": "report.pdf"}))
    assert first["content_sha256"] != second["content_sha256"]
    assert second["pages"][0]["issues"] == []
    assert list(tmp_path.iterdir()) == [pdf]
    with pytest.raises(ValueError, match="Unexpected"):
        asyncio.run(tools.call("pdf_inspect", {"path": "report.pdf", "render_dir": "images"}))
    requests = [{"jsonrpc": "2.0", "id": 1, "method": "initialize"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "pdf_inspect", "arguments": {"path": "report.pdf"}}}]
    import io
    output = io.StringIO()
    serve(tmp_path, io.StringIO("\n".join(json.dumps(r) for r in requests) + "\n"), output, context_only=True)
    response = json.loads(output.getvalue().splitlines()[-1])["result"]
    assert response["isError"] is False
    assert json.loads(response["content"][0]["text"])["visual_review_completed"] is False


def test_workspace_escape_and_internal_state_blocked(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (tmp_path / "outside.pdf").write_bytes(fixture_pdf())
    with pytest.raises(DocumentError, match="inside"):
        inspect_workspace_pdf(workspace, "../outside.pdf")
    for internal in (".smara", ".git", ".codex", ".aws", ".ssh"):
        directory = workspace / internal
        directory.mkdir()
        (directory / "state.pdf").write_bytes(fixture_pdf())
        with pytest.raises(DocumentError, match="internal"):
            inspect_workspace_pdf(workspace, internal + "/state.pdf")


def test_missing_renderer_is_actionable_and_not_false_success(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "pypdfium2", None)
    with pytest.raises(DocumentError, match="not installed"):
        inspect_pdf(fixture_pdf())


def test_rendered_png_export_is_explicit_cli_operation(tmp_path):
    output = tmp_path / "pages"
    inspect_pdf(fixture_pdf(), render_dir=output)
    from PIL import Image
    with Image.open(output / "page-001.png") as page:
        assert page.width > 600
        assert page.height > 800


def test_offline_cli_and_frozen_desktop_dispatch_share_renderer(tmp_path, monkeypatch, capsys):
    from smara import native_runtime, native_management
    path = tmp_path / "report.pdf"
    path.write_bytes(fixture_pdf())
    monkeypatch.setattr("smara.native_profiles.load_profiles", lambda: pytest.fail("PDF command loaded model credentials"))
    assert native_runtime.main(["pdf", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["visual_review_completed"] is False
    assert native_management.main(["--native-pdf", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["pages"][0]["rendered"]
    path.unlink()
    assert native_runtime.main(["pdf", str(path)]) == 2
    assert "not accessible" in capsys.readouterr().err
