from pathlib import Path

from smara.config import _secret


def test_secret_file_fallback_is_used_when_direct_value_is_absent(tmp_path: Path, monkeypatch):
    path = tmp_path / "secret"
    path.write_text("  value-from-file\n", encoding="utf-8")
    monkeypatch.delenv("TEST_SECRET", raising=False)
    monkeypatch.setenv("TEST_SECRET_FILE", str(path))
    assert _secret("TEST_SECRET") == "value-from-file"


def test_direct_secret_wins_over_secret_file(tmp_path: Path, monkeypatch):
    path = tmp_path / "secret"
    path.write_text("file-value", encoding="utf-8")
    monkeypatch.setenv("TEST_SECRET", "direct-value")
    monkeypatch.setenv("TEST_SECRET_FILE", str(path))
    assert _secret("TEST_SECRET") == "direct-value"
