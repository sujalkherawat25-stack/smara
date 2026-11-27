"""Browser engine: tabs, downloads and uploads on generic local pages, plus the policy gates for them.

Downloads and uploads run only in the owner's own PC browser (FILES_ALLOWED, switched on by the helper's run loop),
always after the owner's tap (policy), and only to or from the Smara Shared folder. Programs are never downloaded."""
import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bridge"))
mb = pytest.importorskip("smara_bridge")
playwright = pytest.importorskip("playwright.async_api")

from src.agent.policy import SessionCtx, pre_tool_use  # noqa: E402

PAGE = """<!doctype html><html><head><title>Statements</title></head><body>
<a id="csv" href="data:text/csv;charset=utf-8,date,amount%0A2026-11-01,1499" download="statement.csv">Download statement</a>
<a id="exe" href="data:application/octet-stream,MZ" download="setup.exe">Download app</a>
<label>Attach invoice <input type="file" id="f" onchange="document.getElementById('got').textContent=this.files[0].name"></label>
<div id="got"></div>
<a href="about:blank" target="_blank" id="newtab">Open help</a>
</body></html>"""


def ref(text: str, role: str, name: str) -> int:
    import re
    m = re.search(r"\[(\d+)\] " + re.escape(role) + ' "' + re.escape(name), text)
    assert m, f"{role} {name!r} not in:\n{text}"
    return int(m.group(1))


@pytest.fixture()
def shared(tmp_path, monkeypatch):
    monkeypatch.setattr(mb, "SHARED_DIR", tmp_path / "Shared")
    monkeypatch.setattr(mb, "FILES_ALLOWED", True)
    (tmp_path / "Shared").mkdir()
    return tmp_path / "Shared"


def drive(steps):
    async def go():
        async with playwright.async_playwright() as p:
            exe = mb.find_browser("auto")
            browser = await p.chromium.launch(headless=True, **({"executable_path": exe} if exe else {}))
            ctx = await browser.new_context(accept_downloads=True)
            page = await ctx.new_page()
            await page.set_content(PAGE)
            try:
                return await steps(page)
            finally:
                await browser.close()
    loop = asyncio.ProactorEventLoop() if sys.platform == "win32" else asyncio.new_event_loop()
    try:
        return loop.run_until_complete(go())
    finally:
        loop.close()


def test_download_is_kept_in_shared_downloads_and_programs_are_refused(shared):
    async def steps(page):
        r = await mb.handle(page, {"action": "read"})
        out = await mb.handle(page, {"action": "download", "ref": ref(r["page"], "link", "Download statement")})
        assert out.get("downloaded") == "Downloads/statement.csv", out
        assert (shared / "Downloads" / "statement.csv").read_text().startswith("date,amount")
        again = await mb.handle(page, {"action": "download", "ref": ref(r["page"], "link", "Download statement")})
        assert again["downloaded"] == "Downloads/statement (1).csv"          # never overwrites an earlier file
        bad = await mb.handle(page, {"action": "download", "ref": ref(r["page"], "link", "Download app")})
        assert "program or script" in bad["error"]
        assert not (shared / "Downloads" / "setup.exe").exists()
    drive(steps)


def test_upload_takes_only_a_file_from_the_shared_folder(shared, tmp_path):
    (shared / "invoice.pdf").write_bytes(b"%PDF-1.4 test")
    (tmp_path / "secret.txt").write_text("outside")
    async def steps(page):
        r = await mb.handle(page, {"action": "read"})
        box = ref(r["page"], "file", "Attach invoice")
        out = await mb.handle(page, {"action": "upload", "ref": box, "value": "invoice.pdf"})
        assert out.get("uploaded") == "invoice.pdf", out
        assert await page.inner_text("#got") == "invoice.pdf"
        escape = await mb.handle(page, {"action": "upload", "ref": box, "value": "../secret.txt"})
        assert "outside the shared folder" in escape["error"]
        missing = await mb.handle(page, {"action": "upload", "ref": box, "value": "nope.pdf"})
        assert "not in the Smara Shared folder" in missing["error"]
    drive(steps)


def test_files_never_move_in_the_server_browser(monkeypatch):
    monkeypatch.setattr(mb, "FILES_ALLOWED", False)
    async def steps(page):
        for action in ("download", "upload"):
            out = await mb.handle(page, {"action": action, "ref": 1, "value": "x.pdf"})
            assert "only in the owner's own PC browser" in out["error"]
    drive(steps)


def test_tabs_list_switch_and_close():
    async def steps(page):
        r = await mb.handle(page, {"action": "read"})
        r = await mb.handle(page, {"action": "click", "ref": ref(r["page"], "link", "Open help")})
        page = mb.current(page)
        tabs = await mb.handle(page, {"action": "tabs"})
        assert [t["tab"] for t in tabs["tabs"]] == [1, 2] and tabs["tabs"][1]["current"] is True
        back = await mb.handle(page, {"action": "tab", "value": "1"})
        assert back["switched_to_tab"] == 1 and back["title"] == "Statements"
        page = mb.current(page)
        again = await mb.handle(page, {"action": "tab", "value": 2})               # and back again: no stale link
        assert again["switched_to_tab"] == 2
        page = mb.current(page)
        closed = await mb.handle(page, {"action": "close_tab"})
        assert closed["closed_tab"] == 2 and closed["title"] == "Statements"
        only = await mb.handle(mb.current(page), {"action": "close_tab"})
        assert "only tab" in only["error"]
        bad = await mb.handle(mb.current(page), {"action": "tab", "value": 7})
        assert "no tab 7" in bad["error"]
    drive(steps)


def test_audit_line_masks_long_numbers_and_keeps_the_screenshot(tmp_path, monkeypatch):
    monkeypatch.setattr(mb, "AUDIT_DIR", tmp_path / "audit")
    mb.audit_step({"job_id": "j1", "command": {"action": "fill", "ref": 3, "value": "Acct 1234 5678 9012"}},
                  {"url": "https://bank.example/", "title": "Pay", "target": '[3] textbox "Note"'}, b"\xff\xd8jpeg")
    files = list((tmp_path / "audit").glob("*.bridge.jsonl"))
    row = json.loads(files[0].read_text(encoding="utf-8").splitlines()[0])
    assert row["source"] == "bridge" and row["action"] == "fill" and row["outcome"] == "ok"
    assert "1234" not in row["detail"]["value"] and row["detail"]["value"].startswith("Acct ")
    assert (tmp_path / "audit" / row["screenshot"]).read_bytes() == b"\xff\xd8jpeg"


# ---- policy -------------------------------------------------------------------------------------------------------
KNOWN = frozenset({"browser_step"})


def ctx(**kw):
    base = dict(id="s", tenant_id="t", trust="TRUSTED", on_device=True, host="bank.example", page="https://bank.example/statements",
                controls='[4] link "Download statement"\n[7] button "Attach invoice"')
    base.update(kw)
    return SessionCtx(**base)


def test_download_and_upload_always_wait_for_the_owner_even_with_a_site_grant():
    d = pre_tool_use(ctx(grants=("bank.example",)), "browser_step", {"action": "download", "ref": 4}, KNOWN)
    assert d.verdict == "approve" and "DOWNLOAD" in d.card and "Download statement" in d.card
    u = pre_tool_use(ctx(), "browser_step", {"action": "upload", "ref": 7, "value": "invoice.pdf"}, KNOWN)
    assert u.verdict == "approve" and "UPLOAD" in u.card and "invoice.pdf" in u.card


def test_files_are_refused_off_device_for_counterparties_and_without_a_path():
    assert pre_tool_use(ctx(on_device=False), "browser_step", {"action": "download", "ref": 4}, KNOWN).verdict == "deny"
    assert pre_tool_use(ctx(trust="UNTRUSTED", party_id="p"), "browser_step", {"action": "download", "ref": 4}, KNOWN).verdict == "deny"
    assert pre_tool_use(ctx(), "browser_step", {"action": "upload", "ref": 7}, KNOWN).verdict == "deny"


def test_tabs_are_free_and_so_is_closing_one():
    assert pre_tool_use(ctx(on_device=False), "browser_step", {"action": "tabs"}, KNOWN).verdict == "allow"
    assert pre_tool_use(ctx(on_device=False), "browser_step", {"action": "tab", "value": "2"}, KNOWN).verdict == "allow"
    assert pre_tool_use(ctx(page="https://news.example/"), "browser_step", {"action": "close_tab", "value": "2"}, KNOWN).verdict == "allow"
    assert pre_tool_use(ctx(on_device=False), "browser_step", {"action": "close_tab"}, KNOWN).verdict == "allow"
    assert pre_tool_use(ctx(), "browser_step", {"action": "evaluate"}, KNOWN).verdict == "deny"
