import os
import subprocess
import sys


def _check_import_policy(enabled, code):
    env = os.environ.copy()
    env.pop("SMARA_ENABLE_DELEGATION", None)
    if enabled:
        env["SMARA_ENABLE_DELEGATION"] = "true"
    # Import-time policy must not reload enums used by spawned workers elsewhere.
    result = subprocess.run(
        [sys.executable, "-c", "import smara.subagent_orchestrator as module\n" + code],
        env=env, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_worker_model_aliases_and_default_opt_in():
    _check_import_policy(False, (
        'assert module.DELEGATION_ENABLED is False\n'
        'assert module.normalize_worker_model("glm5.2") == "glm5.2"\n'
        'assert module.normalize_worker_model("glm5.3") == "glm5.3"\n'
        'assert module.normalize_worker_model("custom-model") == "custom-model"\n'
    ))


def test_delegation_requires_explicit_environment_opt_in():
    _check_import_policy(True, "assert module.DELEGATION_ENABLED is True")
