import json
import subprocess
import pytest
from scripts.native_release_manifest import source_manifest, verify_source


def repository(tmp_path):
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    for path, content in {"release/VERSION": "0.1.23", "src/smara/native_runtime.py": "# synthetic", "reports/user.md": "user report"}.items():
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "-c", "user.name=Synthetic", "-c", "user.email=fixture@example.invalid", "commit", "--quiet", "-m", "fixture"], check=True)
    return tmp_path


def test_manifest_allows_unrelated_user_deletion_but_not_build_input_mutation(tmp_path):
    root = repository(tmp_path)
    (root / "reports/user.md").unlink()
    captured = source_manifest(root)
    assert captured["unrelated_changes"] == ["reports/user.md"]
    assert verify_source(root, captured)["build_input_sha256"] == captured["build_input_sha256"]
    (root / "src/smara/native_runtime.py").write_text("changed")
    with pytest.raises(ValueError, match="Commit build inputs"): verify_source(root, captured)


def test_untracked_source_or_committed_change_invalidates_exact_build(tmp_path):
    root = repository(tmp_path)
    captured = source_manifest(root)
    path = root / "src/smara/new.py"
    path.write_text("# new synthetic input")
    with pytest.raises(ValueError): source_manifest(root)
    subprocess.run(["git", "-C", str(root), "add", str(path)], check=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.name=Synthetic", "-c", "user.email=fixture@example.invalid", "commit", "--quiet", "-m", "new input"], check=True)
    with pytest.raises(ValueError, match="Source changed"): verify_source(root, captured)
