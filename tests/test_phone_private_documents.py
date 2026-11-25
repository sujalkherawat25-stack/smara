"""A phone task that reads the owner's own document: it must not fail on that document's layout, lose its answer,
upload anything without saying so, or leave copies behind."""
import os
import time
from pathlib import Path

from smara import native_hosted
from smara.native_goal_runtime import checkpoint_from, partial_checkpoint
from smara.native_outcomes import review, validate_acceptance
from smara.native_documents import language_code
from smara.native_phone_cleanup import asked_to_keep, asked_to_keep_pictures, clean_up, drop_stale_mentions

from test_native_hosted import setup

ANSWER = "It is a 2-page Udyam Registration Certificate. I left out the ID numbers and personal details."


def _old(path: Path, content: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    then = time.time() - 3 * 86400
    os.utime(path, (then, then))
    return path


def test_a_copy_of_the_owners_pdf_is_not_layout_checked_but_a_new_pdf_is(tmp_path):
    started = time.time()
    _old(tmp_path / "UDYAM.pdf", b"%PDF-1.4 the owner's own certificate")
    (tmp_path / "report.pdf").write_bytes(b"%PDF-1.4 written by this task")
    spec = {"workspace": str(tmp_path)}
    only_copy = review(spec, {"outputs": ["UDYAM.pdf"]}, {"turns": []}, since=started)
    assert only_copy["passed"] and only_copy["brought_in"] == ["UDYAM.pdf"]
    with_new = review(spec, {"outputs": ["UDYAM.pdf", "report.pdf"]}, {"turns": []}, since=started)
    failed = [c for c in with_new["checks"] if not c["passed"]]
    assert [c["name"] for c in failed] == ["PDF layout preflight"] and "report.pdf" in failed[0]["detail"]
    (tmp_path / "UDYAM.pdf").unlink()                                     # cleaned up after the task: still acceptable
    validate_acceptance({"spec": spec, "checkpoint": {"outcome": only_copy}})


class Session:
    observed_items = {"1": {"id": "1", "type": "webSearch", "status": "completed"}}


def test_an_interrupted_cycle_keeps_the_earlier_answer(tmp_path):
    goal = {"spec": {"workspace": str(tmp_path)}, "checkpoint": {"summary": ANSWER}, "created": time.time()}
    first = partial_checkpoint(Session(), goal, 1)
    assert first["summary"].startswith("Cycle interrupted") and first["last_answer"] == ANSWER
    again = partial_checkpoint(Session(), {**goal, "checkpoint": first}, 1)       # interrupted twice
    assert again["last_answer"] == ANSWER
    nothing_yet = partial_checkpoint(Session(), {**goal, "checkpoint": {}}, 1)
    assert "last_answer" not in nothing_yet
    forged = checkpoint_from('{"decision":"continue","summary":"s","next_step":"","plan":[],"completed_steps":[],'
                             '"evidence":[],"outputs":[],"last_answer":"x"}', str(tmp_path))
    assert "last_answer" not in forged


def test_out_of_allowance_shows_the_answer_so_far_and_deny_sends_it(tmp_path, monkeypatch):
    store, server, conn = setup(tmp_path, monkeypatch)
    conn.pick_up(wait=0)
    goal = store.claim()
    long_answer = ANSWER + " More detail." * 200
    store.finish(store.get(goal["id"]) | {"lease": goal["lease"]},
                 {"summary": "Cycle interrupted before the final checkpoint.", "last_answer": long_answer},
                 "paused", "Cumulative goal inference budget reached")
    conn.relay()
    ask = server.sent("/t-1/ask")[0]
    assert ANSWER in ask["summary"] and "checks were not completed" in ask["summary"]
    assert len(ask["summary"]) <= native_hosted.ASK_CHARS
    server.answers = [{"task_id": "t-1", "request_id": ask["request_id"], "kind": "approval", "answer": "deny"}]
    conn.apply_answers()
    result = server.sent("/t-1/result")[0]
    assert result["state"] == "failed" and ANSWER in result["summary"] and "checks were not completed" in result["summary"]
    assert "Cycle interrupted" not in result["summary"]


def _tool_approval(tool, params, server="smara_readers", description="Upload one selected file."):
    return native_hosted.describe("mcpServer/elicitation/request", {
        "serverName": server, "message": f'Allow the {server} MCP server to run tool "{tool}"?',
        "_meta": {"codex_approval_kind": "mcp_tool_call", "tool_description": description, "tool_params": params}})


def test_an_upload_approval_says_what_leaves_the_pc_and_where_it_goes():
    picture = _tool_approval("image_analyze", {"path": "pdf-previews/UDYAM/page-001.png", "question": "What is this?"})
    for words in ("UPLOAD A PICTURE", "Sarvam", "pdf-previews/UDYAM/page-001.png", "private details", "charge", "What is this?"):
        assert words in picture
    whole = _tool_approval("document_ocr", {"path": "bill.pdf"})
    assert "UPLOAD A FILE" in whole and "Sarvam" in whole and "bill.pdf" in whole and "whole document" in whole
    assert len(picture) <= native_hosted.ASK_CHARS


def test_other_tool_approvals_show_what_they_are_given_and_hide_secrets():
    text = _tool_approval("browser_fill_code", {"code": "482913", "ref": "e7"}, server="smara_browser",
                          description="Type a one-time code into the page.")
    assert "Type a one-time code" in text and "ref: e7" in text and "482913" not in text and "code: (hidden)" in text
    typed = _tool_approval("browser_act", {"password": "hunter2", "action": "click"}, server="smara_browser")
    assert "hunter2" not in typed and "action: click" in typed


def _phone_folder(tmp_path):
    home, folder = tmp_path / "home", tmp_path / "home" / "Documents" / "Smara phone tasks"
    folder.mkdir(parents=True)
    return home, folder


def test_cleanup_removes_page_pictures_and_redundant_copies_only(tmp_path):
    home, folder = _phone_folder(tmp_path)
    older = folder / "kept-before.txt"
    older.write_text("was here before the task")
    time.sleep(0.05)
    started = time.time() + 2.5            # `older` counts as made before the task (the check allows 2 seconds of slack)
    time.sleep(2.6)
    original = _old(home / "Downloads" / "UDYAM.pdf", b"certificate bytes")
    copy = _old(folder / "UDYAM.pdf", b"certificate bytes")
    only_one = _old(folder / "moved-here.pdf", b"no other copy of this exists")
    made = folder / "dues.xlsx"
    made.write_bytes(b"new work")
    picture = folder / "pdf-previews" / "UDYAM" / "page-001.png"
    picture.parent.mkdir(parents=True)
    picture.write_bytes(b"png")
    removed = clean_up(str(folder), started, "Find udyam.pdf and say what it is. Do not change or copy anything.", home)
    assert sorted(removed) == ["UDYAM.pdf", "pdf-previews/UDYAM/page-001.png"]
    assert original.exists() and only_one.exists() and made.exists() and older.exists()
    assert not copy.exists() and not (folder / "pdf-previews").exists()


def test_cleanup_keeps_everything_when_the_owner_asked_for_a_copy(tmp_path):
    home, folder = _phone_folder(tmp_path)
    started = time.time()
    _old(home / "Downloads" / "bill.pdf", b"bill")
    copy = _old(folder / "bill.pdf", b"bill")
    assert clean_up(str(folder), started, "Copy bill.pdf into the phone tasks folder", home) == [] and copy.exists()
    assert asked_to_keep("Keep the page pictures too") and not asked_to_keep("Read-only. Don't save or copy anything")


def test_a_finished_phone_task_cleans_up_before_it_replies(tmp_path, monkeypatch):
    store, server, conn = setup(tmp_path, monkeypatch)
    conn.pick_up(wait=0)
    goal = store.claim()
    picture = tmp_path / "pdf-previews" / "UDYAM" / "page-001.png"
    picture.parent.mkdir(parents=True)
    picture.write_bytes(b"png")
    store.finish(store.get(goal["id"]) | {"lease": goal["lease"]},
                 {"summary": ANSWER, "evidence": ["read"], "outputs": ["pdf-previews/UDYAM/page-001.png"]},
                 "ready_for_review", "")
    conn.relay()
    text = server.sent("/t-1/result")[0]["summary"]
    assert text.startswith(ANSWER) and "I removed the temporary copies" in text and "Files on your PC" not in text
    assert not picture.exists()


def test_cropped_pictures_go_too_whatever_they_are_called(tmp_path):
    home, folder = _phone_folder(tmp_path)
    started = time.time()
    made = folder / "pdf-previews" / "smara-test-certificate"
    made.mkdir(parents=True)
    for name in ("page-001.png", "top-page-001.png", "top-page-002.PNG", "crop.jpg"):
        (made / name).write_bytes(b"picture")
    (made / "notes.txt").write_text("not a picture")
    removed = clean_up(str(folder), started, "What kind of document is it?", home)
    assert sorted(Path(r).name for r in removed) == ["crop.jpg", "page-001.png", "top-page-001.png", "top-page-002.PNG"]
    assert (made / "notes.txt").exists()


def test_the_reply_does_not_say_a_removed_copy_is_still_saved():
    reply = chr(10).join([
        "Found: C:/Users/me/Downloads/smara-test-certificate.pdf (hyphenated near-match).",
        "Document type: sample course-completion certificate.",
        "The original file was not changed. A read-only copy is saved at C:/Docs/Smara phone tasks/smara-test-certificate.pdf.",
        "Page pictures are in pdf-previews/smara-test-certificate."])
    text = drop_stale_mentions(reply, ["smara-test-certificate.pdf", "pdf-previews/smara-test-certificate/page-001.png"])
    assert "copy is saved" not in text and "pdf-previews" not in text
    assert "Found: C:/Users/me/Downloads/smara-test-certificate.pdf" in text and "The original file was not changed." in text
    assert "Document type: sample course-completion certificate." in text
    assert drop_stale_mentions(reply, []) == reply
    other = "I saved dues.xlsx in the folder." + chr(10) * 2 + "Total due: 7040."
    assert drop_stale_mentions(other, ["UDYAM.pdf"]) == other


def test_a_language_written_as_a_word_is_accepted():
    for given, code in (("English", "en-IN"), ("hindi", "hi-IN"), ("en", "en-IN"), ("en_in", "en-IN"), ("", "en-IN"),
                        ("mr-IN", "mr-IN"), ("Klingon", "Klingon")):
        assert language_code(given) == code


def test_phone_tasks_say_how_to_find_files(tmp_path, monkeypatch):
    store, _, conn = setup(tmp_path, monkeypatch)
    conn.pick_up(wait=0)
    objective = store.claim()["spec"]["objective"]
    assert "hyphens or underscores" in objective and "never AppData" in objective and "under pdf-previews/" in objective


def test_a_copy_inside_a_subfolder_is_removed_with_the_folder_made_for_it(tmp_path):
    home, folder = _phone_folder(tmp_path)
    kept_folder = folder / "reports"
    kept_folder.mkdir()
    time.sleep(2.6)
    started = time.time()
    _old(home / "Downloads" / "smara-test-certificate.pdf", b"fake certificate")
    nested = _old(folder / ".reading-copy" / "deeper" / "smara-test-certificate.pdf", b"fake certificate")
    in_old_folder = _old(kept_folder / "smara-test-certificate.pdf", b"fake certificate")
    no_original = _old(folder / "work" / "only-here.pdf", b"nowhere else")
    internal = _old(folder / ".smara" / "state.pdf", b"fake certificate")
    removed = clean_up(str(folder), started, "What kind of document is it?", home)
    assert sorted(removed) == [".reading-copy/deeper/smara-test-certificate.pdf", "reports/smara-test-certificate.pdf"]
    assert not (folder / ".reading-copy").exists() and not nested.exists() and not in_old_folder.exists()
    assert kept_folder.exists() and no_original.exists() and internal.exists()
    assert (home / "Downloads" / "smara-test-certificate.pdf").exists()


COMMAND = (r""""C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" -Command '$target = Resolve-Path """
           r"""-LiteralPath ".reading-copy'"\\smara-test-certificate.pdf\"; if ($true) { Remove-Item -LiteralPath $target.Path -Force }'""")


def test_a_command_approval_leads_with_what_it_can_do():
    card = native_hosted.describe("item/commandExecution/requestApproval", {"command": COMMAND})
    first = card.split(chr(10))[0]
    assert "can DELETE files" in first and "Remove-Item" not in first
    assert "Files it names: smara-test-certificate.pdf" in card and "Full command:" in card
    assert len(card) <= native_hosted.ASK_CHARS
    long = native_hosted.describe("item/commandExecution/requestApproval", {"command": "del a.txt " + "x" * 3000})
    assert len(long) <= native_hosted.ASK_CHARS and "see it on the PC" in long


def test_command_words_never_promise_a_command_is_harmless():
    effects = native_hosted.command_effects
    assert "no delete, change or internet step" in effects("Get-ChildItem C:/Users -Recurse 2>$null")
    assert "cannot check every effect" in effects("Get-Process | Sort-Object CPU")
    assert "create or change" in effects("Get-Process > processes.txt")
    assert "use the internet" in effects("Invoke-WebRequest https://example.com -OutFile a.zip")
    assert "DELETE files, and create or change" in effects("Copy-Item a.txt b.txt; Remove-Item a.txt")
    assert "HIDDEN" in effects("powershell -EncodedCommand SQBFAFgAIAAoAE4AZQB3AC0A")
    for text in ("Remove-Item x", "rm -r x", "[IO.File]::Delete('x')", "python -c \"import shutil; shutil.rmtree('x')\"",
                 "node -e \"require('fs').rmSync('x')\"", "cmd /c del x", "(Get-Item x).Delete()"):
        assert "DELETE files" in effects(text), text


def test_the_model_is_told_not_to_delete_its_own_copies(tmp_path, monkeypatch):
    store, _, conn = setup(tmp_path, monkeypatch)
    conn.pick_up(wait=0)
    assert "do not delete them yourself" in store.claim()["spec"]["objective"]


def test_file_names_are_listed_without_the_words_around_them():
    effects = native_hosted.command_effects
    assert effects("python report.py").endswith("Files it names: report.py")
    assert effects('Remove-Item "C:/Docs/Smara test certificate.pdf"').endswith("Files it names: Smara test certificate.pdf")
    assert effects("Copy-Item C:/a/one.pdf two.pdf").endswith("Files it names: one.pdf, two.pdf")


def test_names_that_look_like_command_words_do_not_count():
    for text in ("Get-Content .reading-copy/notes.md", "Get-Item C:/old-copy/del.txt", "Get-ChildItem -Filter *.md"):
        assert "no delete, change or internet step" in native_hosted.command_effects(text), text
    only_delete = native_hosted.command_effects("Remove-Item .reading-copy/a.pdf")
    assert only_delete.startswith("run a command on your PC that can DELETE files.")


def test_saving_a_document_does_not_keep_the_pictures_made_to_check_it(tmp_path):
    home, folder = _phone_folder(tmp_path)
    started = time.time()
    picture = folder / "pdf-previews" / "dues-summary" / "page-001.png"
    picture.parent.mkdir(parents=True)
    picture.write_bytes(b"png")
    made = folder / "dues-summary.pdf"
    made.write_bytes(b"%PDF new")
    task = ("Create a ONE-PAGE PDF summary of the dues. Save the PDF in the folder C:\\Users\\me\\Documents\\Smara phone "
            "tasks. Do NOT send the file to anyone, just save it locally. Do not modify the original CSV.")
    assert clean_up(str(folder), started, task, home) == ["pdf-previews/dues-summary/page-001.png"]
    assert made.exists() and not (folder / "pdf-previews").exists()


def test_pictures_stay_when_the_owner_asks_for_pictures(tmp_path):
    home, folder = _phone_folder(tmp_path)
    started = time.time()
    picture = folder / "pdf-previews" / "bill" / "page-001.png"
    picture.parent.mkdir(parents=True)
    picture.write_bytes(b"png")
    for task in ("Save each page of bill.pdf as a picture", "Convert bill.pdf into PNG images and keep them",
                 "Take a screenshot of the bill. Save it."):
        assert clean_up(str(folder), started, task, home) == [] and picture.exists(), task
    for task in ("Save the PDF.", "Read the scanned image and save a summary. Do not keep any pictures.",
                 "What kind of document is this scanned picture?"):
        assert not asked_to_keep_pictures(task), task


def test_a_requested_copy_is_still_kept_while_pictures_go(tmp_path):
    home, folder = _phone_folder(tmp_path)
    started = time.time()
    _old(home / "Downloads" / "bill.pdf", b"bill")
    copy = _old(folder / "bill.pdf", b"bill")
    picture = folder / "pdf-previews" / "bill" / "page-001.png"
    picture.parent.mkdir(parents=True)
    picture.write_bytes(b"png")
    assert clean_up(str(folder), started, "Copy bill.pdf into the phone tasks folder", home) == ["pdf-previews/bill/page-001.png"]
    assert copy.exists()


def test_phone_tasks_say_not_to_invent_names(tmp_path, monkeypatch):
    store, _, conn = setup(tmp_path, monkeypatch)
    conn.pick_up(wait=0)
    assert "do not add a business, person or brand name" in store.claim()["spec"]["objective"]
