"""Real local Git fixtures, not live-model or installed Desktop acceptance."""
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

from smara import native_changes as changes
from smara import native_management, native_runtime


def git(root, *args):
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, check=True)
    return result.stdout.decode("utf-8")


@pytest.fixture
def repo(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    state = tmp_path / "state" / "desktop.json"
    state.parent.mkdir()
    monkeypatch.setenv("SMARA_DESKTOP_STATE", str(state))
    (state.parent / "native-settings.json").write_text(json.dumps({"projects": [{"workspace": str(root), "label": "Test"}], "workspace": str(root)}))
    git(root, "init")
    git(root, "config", "user.name", "Synthetic test")
    git(root, "config", "user.email", "test@example.invalid")
    git(root, "config", "core.autocrlf", "false")
    (root / "code.txt").write_bytes("".join(f"line {n}\n" for n in range(80)).encode("utf-8"))
    git(root, "add", "code.txt")
    git(root, "commit", "-m", "Synthetic fixture")
    return root


def request(root, operation="status", **values):
    return changes.operate({"workspace": str(root), "operation": operation, **values})


def apply(root, review, action, **values):
    return request(root, "apply", token=review["token"], action=action, confirmed=True, **values)


def edit(root, old, new):
    path = root / "code.txt"
    path.write_bytes(path.read_bytes().replace(old.encode(), new.encode()))


def test_real_git_hunk_stage_unstage_and_revert_preserve_other_edits(repo):
    edit(repo, "line 10\n", "first edit\n")
    edit(repo, "line 60\n", "second edit\n")
    review = request(repo)
    assert review["files"][0]["hunks"] == 2
    detail = request(repo, "file", token=review["token"], path="code.txt")
    assert "+first edit" in detail["patch"] and "+second edit" in detail["patch"]
    apply(repo, review, "stage", path="code.txt", hunk=0)
    assert "first edit" in git(repo, "diff", "--cached")
    assert "second edit" not in git(repo, "diff", "--cached")
    staged = request(repo, view="staged")
    apply(repo, staged, "unstage", path="code.txt", hunk=0)
    assert not git(repo, "diff", "--cached")
    result = apply(repo, request(repo), "revert", path="code.txt", hunk=0)
    assert "first edit" not in (repo / "code.txt").read_text()
    assert "second edit" in (repo / "code.txt").read_text()
    saved = changes.load(result["recovery"], "recovery", repo.resolve())
    assert b"first edit" in base64.b64decode(saved["files"]["code.txt"])
    request(repo, "recover", recovery=result["recovery"], confirmed=True)
    assert "first edit" in (repo / "code.txt").read_text()
    assert "second edit" in (repo / "code.txt").read_text()


def test_revert_unstaged_preserves_preexisting_staged_change(repo):
    edit(repo, "line 10\n", "staged edit\n")
    git(repo, "add", "code.txt")
    edit(repo, "line 60\n", "unstaged edit\n")
    apply(repo, request(repo), "revert", path="code.txt")
    assert "staged edit" in (repo / "code.txt").read_text()
    assert "unstaged edit" not in (repo / "code.txt").read_text()
    assert "staged edit" in git(repo, "diff", "--cached")


def test_turn_undo_keeps_preexisting_and_later_manual_edits(repo):
    edit(repo, "line 2\n", "preexisting manual edit\n")
    git(repo, "add", "code.txt")
    index_before = git(repo, "diff", "--cached")
    baseline = request(repo, "capture", thread_id="chat")
    edit(repo, "line 30\n", "agent change\n")
    request(repo, "seal", snapshot=baseline["snapshot"])
    edit(repo, "line 70\n", "later manual edit\n")
    review = request(repo, view="turn", thread_id="chat")
    assert review["files"][0]["hunks"] == 1
    apply(repo, review, "undo")
    value = (repo / "code.txt").read_text()
    assert "agent change" not in value
    assert "preexisting manual edit" in value and "later manual edit" in value
    assert git(repo, "diff", "--cached") == index_before


def test_turn_snapshot_reads_unchanged_baseline_from_local_git_blob(repo):
    baseline = request(repo, "capture", thread_id="chat")
    assert changes.load(baseline["snapshot"], "baseline", repo.resolve())["files"] == {}
    edit(repo, "line 30\n", "agent change\n")
    request(repo, "seal", snapshot=baseline["snapshot"])
    apply(repo, request(repo, view="turn", thread_id="chat"), "undo")
    assert not git(repo, "diff")


def test_conflicting_later_edit_refuses_whole_batch_without_partial_write(repo):
    other = repo / "other.txt"
    other.write_text("original\n")
    git(repo, "add", "other.txt")
    git(repo, "commit", "-m", "Second file")
    baseline = request(repo, "capture", thread_id="chat")
    edit(repo, "line 30\n", "agent change\n")
    other.write_text("agent second\n")
    request(repo, "seal", snapshot=baseline["snapshot"])
    edit(repo, "agent change\n", "later conflicting edit\n")
    review = request(repo, view="turn", thread_id="chat")
    with pytest.raises(ValueError, match="safely"):
        apply(repo, review, "undo")
    assert other.read_text() == "agent second\n"
    assert "later conflicting edit" in (repo / "code.txt").read_text()


@pytest.mark.parametrize("index_change", [False, True])
def test_stale_review_never_overwrites_new_file_or_index_edits(repo, index_change):
    edit(repo, "line 30\n", "reviewed change\n")
    review = request(repo)
    if index_change:
        git(repo, "add", "code.txt")
    else:
        edit(repo, "line 70\n", "new user edit\n")
    before = (repo / "code.txt").read_bytes()
    with pytest.raises(ValueError, match="changed after review"):
        apply(repo, review, "revert", path="code.txt")
    assert (repo / "code.txt").read_bytes() == before


@pytest.mark.parametrize("name", ["new file.txt", "नया 😀.txt", "-option.txt", "empty.txt"])
def test_new_files_are_reversible_and_recovery_is_kept(repo, name):
    (repo / name).write_text("" if name == "empty.txt" else "synthetic text\n", encoding="utf-8")
    review = request(repo)
    apply(repo, review, "stage", path=name)
    assert name in git(repo, "ls-files", "-z").split("\0")
    apply(repo, request(repo, view="staged"), "unstage", path=name)
    result = apply(repo, request(repo), "revert", path=name)
    assert not (repo / name).exists()
    assert name in changes.load(result["recovery"], "recovery", repo.resolve())["files"]


@pytest.mark.parametrize("newline", [b"\n", b"\r\n", b""])
def test_turn_undo_preserves_exact_line_endings_and_no_final_newline(repo, newline):
    path = repo / "code.txt"
    before = b"first" + (newline or b"\n") + b"last" + newline
    path.write_bytes(before)
    git(repo, "add", "code.txt")
    git(repo, "commit", "-m", "Line ending fixture")
    baseline = request(repo, "capture", thread_id="chat")
    path.write_bytes(before.replace(b"last", b"changed"))
    request(repo, "seal", snapshot=baseline["snapshot"])
    apply(repo, request(repo, view="turn", thread_id="chat"), "undo")
    assert path.read_bytes() == before


def test_crlf_checkout_with_autocrlf_is_reversible(repo):
    git(repo, "config", "core.autocrlf", "true")
    path = repo / "code.txt"
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    before = path.read_bytes()
    baseline = request(repo, "capture", thread_id="chat")
    path.write_bytes(before.replace(b"line 30", b"changed"))
    request(repo, "seal", snapshot=baseline["snapshot"])
    apply(repo, request(repo, view="turn", thread_id="chat"), "undo")
    assert path.read_bytes() == before


def test_ignored_binary_and_filtered_files_are_not_silently_claimed_undoable(repo):
    (repo / ".gitignore").write_text("private.key\n")
    (repo / "private.key").write_text("synthetic sensitive value")
    (repo / "binary.bin").write_bytes(b"\0binary")
    (repo / ".gitattributes").write_text("filtered.txt filter=synthetic\n")
    (repo / "filtered.txt").write_text("synthetic filtered text")
    git(repo, "config", "filter.synthetic.clean", "echo FILTER_RAN > filter-marker")
    value = request(repo)
    assert not (repo / "filter-marker").exists()
    assert "private.key" not in json.dumps(value)
    assert any("binary.bin" in note for note in value["notes"])
    assert any("filtered.txt" in note for note in value["notes"])


def test_project_boundary_and_review_token_cross_project_access(repo):
    with pytest.raises(ValueError, match="exact folder"):
        request(repo.parent)
    sub = repo / "sub"
    sub.mkdir()
    native_management.manage({"operation": "add_project", "workspace": str(sub)})
    with pytest.raises(ValueError, match="parent repositories"):
        request(sub)
    edit(repo, "line 30\n", "changed\n")
    token = request(repo)["token"]
    with pytest.raises(ValueError, match="another project"):
        changes.load(token, "review", repo.parent)
    for name in ["../outside", ".git/config", "/outside", "C:/outside", "sub/../../outside"]:
        with pytest.raises(ValueError):
            changes.Repository(str(repo)).path(name)


def test_symlink_destination_is_not_read_or_changed(repo):
    target = repo.parent / "outside.txt"
    target.write_text("outside synthetic data")
    try:
        (repo / "link.txt").symlink_to(target)
    except OSError:
        pytest.skip("Windows account cannot create symlinks")
    result = request(repo)
    assert any("Symlinks" in note for note in result["notes"])
    assert target.read_text() == "outside synthetic data"


def test_explicit_confirmation_and_matching_view_are_required(repo):
    edit(repo, "line 30\n", "changed\n")
    token = request(repo)["token"]
    with pytest.raises(ValueError, match="explicit confirmation"):
        request(repo, "apply", token=token, action="revert", path="code.txt")
    with pytest.raises(ValueError, match="matching"):
        request(repo, "apply", token=token, action="undo", path="code.txt", confirmed=True)
    assert "changed" in (repo / "code.txt").read_text()


def test_head_change_refuses_turn_undo_and_cli_needs_no_model(repo, capsys):
    baseline = request(repo, "capture", thread_id="chat")
    edit(repo, "line 30\n", "changed\n")
    git(repo, "add", "code.txt")
    git(repo, "commit", "-m", "New commit during turn")
    with pytest.raises(ValueError, match="HEAD changed"):
        request(repo, "seal", snapshot=baseline["snapshot"])
    assert native_runtime.main(["changes", "status", "--workspace", str(repo), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["view"] == "unstaged"


def test_repository_environment_cannot_redirect_review(repo, monkeypatch):
    monkeypatch.setenv("GIT_DIR", str(repo.parent / "other.git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(repo.parent))
    edit(repo, "line 30\n", "changed\n")
    assert request(repo)["files"][0]["path"] == "code.txt"


def test_recovery_refuses_later_edits_and_keeps_them(repo):
    edit(repo, "line 30\n", "reviewed edit\n")
    result = apply(repo, request(repo), "revert", path="code.txt")
    edit(repo, "line 70\n", "later edit\n")
    with pytest.raises(ValueError, match="changed since"):
        request(repo, "recover", recovery=result["recovery"], confirmed=True)
    assert "later edit" in (repo / "code.txt").read_text()


def test_branch_review_is_committed_read_only_and_does_not_fetch(repo):
    base = git(repo, "branch", "--show-current").strip()
    git(repo, "switch", "-c", "synthetic-feature")
    edit(repo, "line 30\n", "committed feature\n")
    git(repo, "add", "code.txt")
    git(repo, "commit", "-m", "Feature fixture")
    edit(repo, "line 70\n", "uncommitted user edit\n")
    review = request(repo, view="branch", base=base)
    detail = request(repo, "file", token=review["token"], path="code.txt")
    assert "committed feature" in detail["patch"]
    assert "uncommitted user edit" not in detail["patch"]
    with pytest.raises(ValueError, match="matching"):
        apply(repo, review, "revert", path="code.txt")


def test_unsealed_latest_run_does_not_expose_older_turn_undo(repo):
    baseline = request(repo, "capture", thread_id="chat")
    edit(repo, "line 30\n", "first turn\n")
    request(repo, "seal", snapshot=baseline["snapshot"])
    assert request(repo, view="turn", thread_id="chat")["files"]
    request(repo, "capture", thread_id="chat")
    result = request(repo, view="turn", thread_id="chat")
    assert not result["files"] and not result["token"]
    assert "no sealed snapshot" in result["notes"][0]


def test_concurrent_read_previews_serialize_without_stale_lock_failure(repo):
    edit(repo, "line 30\n", "changed\n")
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: request(repo), range(2)))
    assert all(result["files"][0]["path"] == "code.txt" for result in results)
    assert results[0]["token"] != results[1]["token"]
    assert request(repo)["files"]


def test_coding_outcome_fix_test_review_and_undo_with_manual_edit(repo):
    """No model: actual pytest exit codes and Git/file behavior are the gate."""
    (repo / ".gitignore").write_text("__pycache__/\n.pytest_cache/\n")
    source = repo / "maths.py"
    source.write_bytes(b"def multiply(a, b):\n    return a + b\n" + b"\n" * 15 + b"# original note\n")
    (repo / "test_maths.py").write_text("from maths import multiply\ndef test_multiply():\n    assert multiply(17, 23) == 391\n")
    git(repo, "add", ".gitignore", "maths.py", "test_maths.py")
    git(repo, "commit", "-m", "Coding outcome fixture")
    def verify():
        # Same-length edits can share an integer mtime. Do not write bytecode in
        # this fresh fixture, so each real pytest run imports the current source.
        result = subprocess.run(
            [sys.executable, "-B", "-m", "pytest", "-q", "test_maths.py"],
            cwd=repo, capture_output=True, timeout=30,
        )
        assert not list((repo / "__pycache__").glob("maths.*.pyc"))
        return result
    assert verify().returncode == 1
    baseline = request(repo, "capture", thread_id="coding")
    source.write_bytes(source.read_bytes().replace(b"return a + b", b"return a * b"))
    assert verify().returncode == 0
    request(repo, "seal", snapshot=baseline["snapshot"])
    source.write_bytes(source.read_bytes().replace(b"# original note", b"# later manual note"))
    review = request(repo, view="turn", thread_id="coding")
    assert request(repo, "file", token=review["token"], path="maths.py")["patch"].count("+    return a * b") == 1
    apply(repo, review, "undo", path="maths.py")
    assert b"return a + b" in source.read_bytes() and b"# later manual note" in source.read_bytes()
    assert verify().returncode == 1


def test_cli_review_is_readable_and_never_needs_a_model(repo, capsys):
    edit(repo, "line 30\n", "changed\n")
    assert native_runtime.main(["changes", "status", "--workspace", str(repo)]) == 0
    output = capsys.readouterr().out
    assert "Changes · unstaged" in output and "code.txt" in output and "Review token:" in output
