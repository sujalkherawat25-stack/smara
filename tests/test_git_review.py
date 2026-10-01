import subprocess
import pytest

from smara.git_agent import GitWorkspaceManager


def git(folder, *args, check=True):
    return subprocess.run(['git', '-C', str(folder), *args], check=check,
                          capture_output=True, text=True)


def repository(tmp_path):
    git(tmp_path, 'init', '-b', 'main')
    git(tmp_path, 'config', 'user.name', 'Acceptance Test')
    git(tmp_path, 'config', 'user.email', 'test@example.invalid')
    (tmp_path / 'sample.txt').write_text('original\n', encoding='utf-8')
    git(tmp_path, 'add', 'sample.txt')
    git(tmp_path, 'commit', '-m', 'initial')
    return GitWorkspaceManager(tmp_path)


def test_first_unstaged_status_preserves_leading_column(tmp_path):
    manager = repository(tmp_path)
    (tmp_path / 'sample.txt').write_text('changed\n', encoding='utf-8')
    status = manager.get_status()
    assert status.unstaged_files == ['sample.txt']
    assert status.staged_files == []


def test_marker_examples_are_not_git_conflicts(tmp_path):
    manager = repository(tmp_path)
    (tmp_path / 'example.md').write_text('<<<<<<< HEAD\nexample\n=======\nother\n>>>>>>> branch\n')
    assert manager.detect_conflicts() == []


def test_non_repository_never_walks_folder(tmp_path, monkeypatch):
    import os
    def forbidden(*args, **kwargs):
        raise AssertionError('Conflict review must not walk a non-repository')
    monkeypatch.setattr(os, 'walk', forbidden)
    assert GitWorkspaceManager(tmp_path).detect_conflicts() == []


def test_actual_unmerged_file_has_desktop_dictionary_shape(tmp_path):
    manager = repository(tmp_path)
    git(tmp_path, 'checkout', '-b', 'other')
    (tmp_path / 'sample.txt').write_text('other\n', encoding='utf-8')
    git(tmp_path, 'commit', '-am', 'other')
    git(tmp_path, 'checkout', 'main')
    (tmp_path / 'sample.txt').write_text('main\n', encoding='utf-8')
    git(tmp_path, 'commit', '-am', 'main')
    assert git(tmp_path, 'merge', 'other', check=False).returncode != 0
    assert manager.detect_conflicts() == [{'file': 'sample.txt', 'path': str(tmp_path / 'sample.txt')}]


@pytest.mark.parametrize('absolute', [False, True])
def test_conflict_resolution_cannot_escape_workspace(tmp_path, absolute):
    workspace = tmp_path / 'project'; workspace.mkdir()
    outside = tmp_path / 'outside.txt'
    original = '<<<<<<< HEAD\na\n=======\nb\n>>>>>>> branch\n'
    outside.write_text(original)
    path = str(outside) if absolute else '../outside.txt'
    ok, message = GitWorkspaceManager(workspace).resolve_conflict(path)
    assert not ok and 'outside the workspace' in message
    assert outside.read_text() == original


def test_valid_conflict_resolution_and_invalid_paths(tmp_path):
    p = tmp_path / 'inside.txt'
    p.write_text('<<<<<<< HEAD\na\n=======\nb\n>>>>>>> branch\n')
    manager = GitWorkspaceManager(tmp_path)
    assert manager.resolve_conflict('inside.txt')[0]
    assert p.read_text() == 'a\n'
    for invalid in ['', None, '\0', str(tmp_path)]:
        assert manager.resolve_conflict(invalid)[0] is False


def test_conflict_resolution_denies_outside_symlink(tmp_path):
    workspace = tmp_path / 'project'; workspace.mkdir()
    outside = tmp_path / 'outside.txt'; outside.write_text('untouched')
    link = workspace / 'link.txt'
    try: link.symlink_to(outside)
    except OSError: pytest.skip('Symlink creation unavailable on this host')
    assert GitWorkspaceManager(workspace).resolve_conflict('link.txt')[0] is False
    assert outside.read_text() == 'untouched'
