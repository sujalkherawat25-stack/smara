from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

import pytest

from smara.sandbox import (
    SandboxLimits,
    build_workspace_docker_argv,
    docker_engine_status,
    run_workspace_command,
)


def _require_docker() -> None:
    status = docker_engine_status()
    if not status.get("engine_available") or not status.get("linux_containers"):
        pytest.skip("A reachable Docker Linux-container engine is required for isolation boundary checks.")


def test_container_plan_has_workspace_only_mount_and_no_network(tmp_path: Path):
    _require_docker()
    workspace = tmp_path / "project"
    workspace.mkdir()
    command = build_workspace_docker_argv(
        ["sh", "-lc", "printf ok"], workspace, image="alpine:3.20",
        container_name="smara-0123456789abcdefabcd",
    )
    assert "--network" in command and command[command.index("--network") + 1] == "none"
    assert "--read-only" in command
    assert "--cap-drop" in command and command[command.index("--cap-drop") + 1] == "ALL"
    mounts = [command[index + 1] for index, item in enumerate(command[:-1]) if item == "--mount"]
    assert mounts == [f"type=bind,source={str(workspace.resolve()).replace(chr(92), '/')},target=/workspace"]
    assert all("/var/run/docker.sock" not in item for item in command)
    assert "--memory" in command and "--cpus" in command and "--pids-limit" in command


def test_container_environment_drops_credentials_and_host_path(tmp_path: Path):
    _require_docker()
    workspace = tmp_path / "project"
    workspace.mkdir()
    command = build_workspace_docker_argv(
        ["python", "-c", "print('ok')"], workspace, image="python:3.12-slim",
        env={"TZ": "UTC", "PATH": "C:/private/bin", "SARVAM_API_KEY": "must-not-leak"},
        container_name="smara-0123456789abcdefabcd",
    )
    passed_env = [command[index + 1] for index, item in enumerate(command[:-1]) if item == "--env"]
    assert "TZ=UTC" in passed_env
    assert all(not item.startswith("PATH=") for item in passed_env)
    assert "SARVAM_API_KEY=must-not-leak" not in command


def test_workspace_command_writes_inside_but_cannot_write_parent(tmp_path: Path):
    _require_docker()
    workspace = tmp_path / "project"
    workspace.mkdir()
    marker = "sandbox-write-ok"
    inside = run_workspace_command(
        ["sh", "-lc", f"printf {marker} > inside.txt"], workspace, image="alpine:3.20", timeout=20,
    )
    assert inside.returncode == 0, inside.stderr
    assert (workspace / "inside.txt").read_text(encoding="utf-8") == marker

    outside = tmp_path / "outside.txt"
    escaped = run_workspace_command(
        ["sh", "-lc", "printf escaped > ../outside.txt"], workspace, image="alpine:3.20", timeout=20,
    )
    assert escaped.returncode != 0
    assert not outside.exists()


def test_workspace_command_cannot_read_host_sibling(tmp_path: Path):
    _require_docker()
    workspace = tmp_path / "project"
    workspace.mkdir()
    secret = "host-only-" + "z" * 32
    host_file = tmp_path / "host-secret.txt"
    host_file.write_text(secret, encoding="utf-8")
    probe = (
        "python3 -c 'import pathlib,sys; "
        "p=pathlib.Path(sys.argv[1]); "
        "print(p.read_text() if p.exists() else \"HOST_FILE_NOT_MOUNTED\")' "
        + shlex.quote(str(host_file))
    )
    result = run_workspace_command(["sh", "-lc", probe], workspace, image="python:3.12-slim", timeout=20)
    assert result.returncode == 0, result.stderr
    assert "HOST_FILE_NOT_MOUNTED" in result.stdout
    assert secret not in result.stdout


def test_workspace_command_cannot_open_external_network(tmp_path: Path):
    _require_docker()
    workspace = tmp_path / "project"
    workspace.mkdir()
    code = (
        "import socket; s=socket.socket(); s.settimeout(1); "
        "\ntry: s.connect(('1.1.1.1',443)); print('NETWORK_CONNECTED')"
        "\nexcept OSError: print('NETWORK_BLOCKED')"
        "\nfinally: s.close()"
    )
    result = run_workspace_command(
        ["python3", "-c", code], workspace, image="python:3.12-slim", timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert "NETWORK_BLOCKED" in result.stdout


def test_workspace_cwd_must_be_inside_approved_root(tmp_path: Path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(ValueError, match="inside the approved workspace"):
        build_workspace_docker_argv(
            ["sh", "-lc", "true"], workspace, outside, image="alpine:3.20",
            limits=SandboxLimits(timeout_seconds=10, memory_mb=256, cpus=0.5, pids=8),
        )


def test_readonly_workspace_blocks_terminal_edits(tmp_path):
    _require_docker()
    result = run_workspace_command(["sh", "-lc", "printf forbidden > bad.txt"], tmp_path,
                                   image="alpine:3.20", read_only_workspace=True, timeout=15)
    assert result.returncode != 0
    assert not (tmp_path / "bad.txt").exists()


def test_container_deadline_is_independent_of_polling(tmp_path):
    _require_docker()
    command = build_workspace_docker_argv(["sh", "-lc", "sleep 30"], tmp_path, image="alpine:3.20",
                                          limits=SandboxLimits(timeout_seconds=1))
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        stdout, stderr = process.communicate(timeout=8)
        assert process.returncode in {124, 137, 143}, (stdout, stderr)
    finally:
        from smara.sandbox import container_name_from_argv, stop_workspace_container
        stop_workspace_container(container_name_from_argv(command))
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def test_coding_image_has_tools_and_runs_real_pytest(tmp_path):
    _require_docker()
    from smara.test_fixer import PytestRunner
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_real.py").write_text("def test_arithmetic():\n    assert 2 + 2 == 4\n")
    result = run_workspace_command(["sh", "-lc", "python3 --version && git --version && node --version && npm --version && pytest --version"], tmp_path)
    assert result.returncode == 0, result.stderr
    tested = PytestRunner(tmp_path).run()
    assert tested.success and tested.passed == tested.total == 1
