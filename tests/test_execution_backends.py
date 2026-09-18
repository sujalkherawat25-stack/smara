from pathlib import Path

import pytest

from smara.sandbox import WSLConfig, docker_command, wsl_command


def test_docker_is_hardened():
    command = docker_command("echo ok")
    assert "--network" in command and "none" in command
    assert "--cap-drop" in command and "ALL" in command
    assert "--pids-limit" in command


def test_wsl_command_rejects_injection():
    assert wsl_command("printf ok", WSLConfig("Ubuntu"))[0] == "wsl.exe"
    with pytest.raises(ValueError):
        wsl_command("true", WSLConfig("Ubuntu;rm"))
