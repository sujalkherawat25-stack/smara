import importlib


def test_worker_model_aliases_and_default_opt_in(monkeypatch):
    import smara.subagent_orchestrator as module

    monkeypatch.delenv("SMARA_ENABLE_DELEGATION", raising=False)
    module = importlib.reload(module)
    assert module.DELEGATION_ENABLED is False
    assert module.normalize_worker_model("glm5.2") == "glm5.2"
    assert module.normalize_worker_model("glm5.3") == "glm5.3"
    assert module.normalize_worker_model("custom-model") == "custom-model"


def test_delegation_requires_explicit_environment_opt_in(monkeypatch):
    monkeypatch.setenv("SMARA_ENABLE_DELEGATION", "true")
    import smara.subagent_orchestrator as module

    module = importlib.reload(module)
    assert module.DELEGATION_ENABLED is True
    monkeypatch.delenv("SMARA_ENABLE_DELEGATION", raising=False)
    importlib.reload(module)
