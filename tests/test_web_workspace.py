from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_legacy_control_ui_is_not_shipped():
    """The API must not expose the retired duplicate static control app."""
    assert not (ROOT / "web").exists()
    assert not (ROOT / "frontend" / "src" / "components" / "Control" / "ControlPanel.tsx").exists()


def test_legacy_mount_config_is_not_shipped():
    active_caddy = (ROOT / "deploy" / "Caddyfile.ai-active").read_text(encoding="utf-8")
    assert "@retired_smara path /smara /smara/*" in active_caddy
    assert "handle @retired_smara" in active_caddy
    assert "control-staging.syntarus.com" not in active_caddy
    assert "handle_path /smara-api/*" in active_caddy
