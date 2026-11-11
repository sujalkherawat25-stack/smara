"""Smara Desktop chat and the hosted WhatsApp agent run the same browser engine. The Desktop copy must be the hosted file,
byte for byte, so the two cannot drift apart again."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_the_desktop_engine_is_the_hosted_engine_file():
    hosted = ROOT / "server" / "bridge" / "smara_bridge.py"
    desktop = ROOT / "src" / "smara" / "smara_bridge.py"
    assert desktop.read_bytes() == hosted.read_bytes(), "copy server/bridge/smara_bridge.py to src/smara/smara_bridge.py"


def test_the_bundled_pc_helper_is_the_hosted_engine_file():
    hosted = ROOT / "server" / "bridge" / "smara_bridge.py"
    bundled = ROOT / "apps" / "desktop" / "src-tauri" / "resources" / "browser" / "smara_bridge.py"
    assert bundled.read_bytes() == hosted.read_bytes(), "copy server/bridge/smara_bridge.py to the Desktop resources"
