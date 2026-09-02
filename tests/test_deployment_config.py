from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_active_caddy_keeps_marketing_and_smara_routes():
    config = (ROOT / "deploy" / "Caddyfile.ai-active").read_text(encoding="utf-8")
    for required in (
        "syntarus.com, www.syntarus.com {",
        "reverse_proxy 127.0.0.1:8082",
        "ai.syntarus.com {",
        "handle_path /smara-api/*",
        "reverse_proxy 127.0.0.1:8090",
        "reverse_proxy 127.0.0.1:8081",
        "@legacy_operator_dashboard path /v1/memento/admin/dashboard /v1/memento/admin/dashboard/*",
        "redir @legacy_operator_dashboard /admin 308",
    ):
        assert required in config


def test_source_caddy_has_the_same_operator_redirect():
    source = ROOT.parent / "caddy" / "Caddyfile"
    if not source.exists():
        source = ROOT / "deploy" / "Caddyfile.ai-active"
    assert source.exists()
    config = source.read_text(encoding="utf-8")
    assert "@legacy_operator_dashboard path /v1/memento/admin/dashboard /v1/memento/admin/dashboard/*" in config
    assert "redir @legacy_operator_dashboard /admin 308" in config


def test_smara_services_restart_after_host_reboot():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    # Native Smara now includes its own Telegram edge as an eighth durable
    # service; all services must come back after a host reboot.
    assert compose.count("restart: unless-stopped") == 8
