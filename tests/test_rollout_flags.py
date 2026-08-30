from __future__ import annotations

from smara.config import Settings, _env_bool, _env_int


def test_rollout_defaults_are_safe_and_bounded(monkeypatch):
    monkeypatch.delenv("SMARA_FAST_ROUTING_ENABLED", raising=False)
    monkeypatch.delenv("SMARA_WORKER_CONCURRENCY", raising=False)
    assert _env_bool("SMARA_FAST_ROUTING_ENABLED", True) is True
    assert _env_int("SMARA_WORKER_CONCURRENCY", 4, minimum=1, maximum=8) == 4


def test_rollout_env_values_are_normalized_and_clamped(monkeypatch):
    monkeypatch.setenv("SMARA_FAST_ROUTING_ENABLED", "off")
    monkeypatch.setenv("SMARA_WORKER_CONCURRENCY", "99")
    assert _env_bool("SMARA_FAST_ROUTING_ENABLED", True) is False
    assert _env_int("SMARA_WORKER_CONCURRENCY", 4, minimum=1, maximum=8) == 8

    monkeypatch.setenv("SMARA_WORKER_CONCURRENCY", "not-a-number")
    assert _env_int("SMARA_WORKER_CONCURRENCY", 4, minimum=1, maximum=8) == 4


def test_settings_expose_operator_rollback_switches():
    settings = Settings(
        fast_routing_enabled=False,
        pooled_resources_enabled=False,
        work_signals_enabled=False,
        desktop_long_poll_enabled=False,
        shadow_routing_enabled=True,
        worker_concurrency=2,
    )
    assert settings.fast_routing_enabled is False
    assert settings.pooled_resources_enabled is False
    assert settings.work_signals_enabled is False
    assert settings.desktop_long_poll_enabled is False
    assert settings.shadow_routing_enabled is True
    assert settings.worker_concurrency == 2
