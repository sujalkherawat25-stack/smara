import subprocess

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
