"""The pairing moves from ~/.munim-bridge to ~/.smara-bridge once, and an unpair does not bring it back."""
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bridge"))
import smara_bridge as sb  # noqa: E402


def _set_homes(monkeypatch, tmp_path, *, home, old):
    monkeypatch.setattr(sb, "DEFAULT_HOME", tmp_path / ".smara-bridge")
    monkeypatch.setattr(sb, "HOME", home)
    monkeypatch.setattr(sb, "OLD_HOME", old)
    monkeypatch.delenv("SMARA_BRIDGE_HOME", raising=False)


def test_the_pairing_moves_once_and_an_unpair_does_not_bring_it_back(tmp_path, monkeypatch):
    old = tmp_path / ".munim-bridge"
    old.mkdir()
    (old / "config.json").write_text('{"server": "https://ai.syntarus.com/smara", "token": "t", "shop": "Demo"}',
                                     encoding="utf-8")
    new = tmp_path / ".smara-bridge"
    _set_homes(monkeypatch, tmp_path, home=new, old=old)

    sb.carry_over_old_home()
    assert (new / "config.json").read_text(encoding="utf-8").startswith('{"server"')
    assert (old / "MOVED_TO_SMARA").exists()

    shutil.rmtree(new)                      # the owner unpairs, and the new folder goes away
    sb.carry_over_old_home()
    assert not new.exists()                 # the old folder must not bring the pairing back


def test_a_custom_home_is_never_moved(tmp_path, monkeypatch):
    old = tmp_path / ".munim-bridge"
    old.mkdir()
    (old / "config.json").write_text("{}", encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    _set_homes(monkeypatch, tmp_path, home=elsewhere, old=old)

    sb.carry_over_old_home()
    assert not elsewhere.exists()
    assert not (old / "MOVED_TO_SMARA").exists()
