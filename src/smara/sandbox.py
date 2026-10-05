"""Bounded Docker execution for coding and workspace terminal commands.

Workspace commands mount only the selected project, with no network access,
restricted resources, and no inherited application secrets. The legacy generic
runner below has no workspace mount and is retained for existing callers.
"""
from __future__ import annotations

import subprocess
import shutil
import os
import platform
import httpx
import re
import uuid
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath


@dataclass(frozen=True)
class SandboxLimits:
    timeout_seconds: int = 60
    memory_mb: int = 256
    cpus: float = 0.5
    pids: int = 64


@dataclass(frozen=True)
class WSLConfig:
    distribution: str = "Ubuntu"
    timeout_seconds: int = 60


DEFAULT_CODING_IMAGE = "smara-coding:local"

_IMAGE_BY_EXECUTABLE = {
    "python": ("SMARA_SANDBOX_PYTHON_IMAGE", "python:3.12-slim"),
    "python.exe": ("SMARA_SANDBOX_PYTHON_IMAGE", "python:3.12-slim"),
    "python3": ("SMARA_SANDBOX_PYTHON_IMAGE", "python:3.12-slim"),
    "python3.exe": ("SMARA_SANDBOX_PYTHON_IMAGE", "python:3.12-slim"),
    "pytest": ("SMARA_SANDBOX_PYTEST_IMAGE", "python:3.12-slim"),
    "pytest.exe": ("SMARA_SANDBOX_PYTEST_IMAGE", "python:3.12-slim"),
    "pwsh": ("SMARA_SANDBOX_POWERSHELL_IMAGE", "mcr.microsoft.com/powershell:lts-debian-12"),
    "pwsh.exe": ("SMARA_SANDBOX_POWERSHELL_IMAGE", "mcr.microsoft.com/powershell:lts-debian-12"),
    "powershell": ("SMARA_SANDBOX_POWERSHELL_IMAGE", "mcr.microsoft.com/powershell:lts-debian-12"),
    "powershell.exe": ("SMARA_SANDBOX_POWERSHELL_IMAGE", "mcr.microsoft.com/powershell:lts-debian-12"),
    "git": ("SMARA_SANDBOX_GIT_IMAGE", "alpine/git:latest"),
    "git.exe": ("SMARA_SANDBOX_GIT_IMAGE", "alpine/git:latest"),
    "node": ("SMARA_SANDBOX_NODE_IMAGE", "node:22-bookworm-slim"),
    "node.exe": ("SMARA_SANDBOX_NODE_IMAGE", "node:22-bookworm-slim"),
    "npm": ("SMARA_SANDBOX_NODE_IMAGE", "node:22-bookworm-slim"),
    "npm.cmd": ("SMARA_SANDBOX_NODE_IMAGE", "node:22-bookworm-slim"),
    "cargo": ("SMARA_SANDBOX_RUST_IMAGE", "rust:slim-bookworm"),
    "cargo.exe": ("SMARA_SANDBOX_RUST_IMAGE", "rust:slim-bookworm"),
    "rustc": ("SMARA_SANDBOX_RUST_IMAGE", "rust:slim-bookworm"),
    "rustc.exe": ("SMARA_SANDBOX_RUST_IMAGE", "rust:slim-bookworm"),
    "go": ("SMARA_SANDBOX_GO_IMAGE", "golang:1.25-bookworm"),
    "go.exe": ("SMARA_SANDBOX_GO_IMAGE", "golang:1.25-bookworm"),
    "sh": ("SMARA_SANDBOX_SHELL_IMAGE", "python:3.12-slim"),
    "sh.exe": ("SMARA_SANDBOX_SHELL_IMAGE", "python:3.12-slim"),
    "bash": ("SMARA_SANDBOX_BASH_IMAGE", "bash:5.2"),
    "bash.exe": ("SMARA_SANDBOX_BASH_IMAGE", "bash:5.2"),
}
for _core_tool in ("python", "python.exe", "python3", "python3.exe", "pytest", "pytest.exe", "git", "git.exe", "node", "node.exe", "npm", "npm.cmd", "sh", "sh.exe", "bash", "bash.exe"):
    _setting, _ = _IMAGE_BY_EXECUTABLE[_core_tool]
    _IMAGE_BY_EXECUTABLE[_core_tool] = (_setting, DEFAULT_CODING_IMAGE)
_CONTAINER_EXECUTABLE = {
    "python.exe": "python3", "python": "python3", "python3.exe": "python3",
    "pytest.exe": "pytest", "pwsh.exe": "pwsh", "powershell.exe": "pwsh",
    "powershell": "pwsh", "git.exe": "git", "node.exe": "node",
    "npm.cmd": "npm", "cargo.exe": "cargo", "rustc.exe": "rustc",
    "go.exe": "go", "sh.exe": "sh", "bash.exe": "bash",
}
_SANDBOX_ENV = {"CI", "LANG", "LC_ALL", "NO_COLOR", "PYTHONIOENCODING", "PYTHONUNBUFFERED", "TZ"}


def _docker_executable() -> str:
    executable = shutil.which("docker")
    if not executable:
        raise RuntimeError("Docker CLI is unavailable; terminal execution is blocked without the sandbox.")
    return executable


def docker_engine_status() -> dict[str, object]:
    executable = shutil.which("docker")
    if not executable:
        return {"cli_available": False, "engine_available": False, "linux_containers": False}
    try:
        result = subprocess.run(
            [executable, "info", "--format", "{{.OSType}} {{.ServerVersion}}"],
            capture_output=True, text=True, timeout=8, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return {"cli_available": True, "engine_available": False, "linux_containers": False}
    value = result.stdout.strip().split()
    if result.returncode != 0 or len(value) != 2:
        return {"cli_available": True, "engine_available": False, "linux_containers": False}
    return {
        "cli_available": True,
        "engine_available": True,
        "linux_containers": value[0].lower() == "linux",
        "server_version": value[1],
    }


def _container_image(argv: list[str], override: str | None = None) -> tuple[str, str]:
    raw_executable = argv[0]
    name = PureWindowsPath(raw_executable).name.lower() if "\\" in raw_executable else Path(raw_executable).name.lower()
    image_setting = _IMAGE_BY_EXECUTABLE.get(name)
    if override:
        image = override
    elif image_setting:
        env_name, default = image_setting
        image = os.environ.get(env_name) or os.environ.get("SMARA_SANDBOX_IMAGE") or default
    else:
        image = os.environ.get("SMARA_SANDBOX_IMAGE")
        if not image:
            raise RuntimeError(
                f"No isolated image is configured for '{name}'. Set SMARA_SANDBOX_IMAGE to a local Linux image containing that tool."
            )
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/:@+-]{0,250}", image):
        raise ValueError("Sandbox image reference contains unsupported characters.")
    return image, _CONTAINER_EXECUTABLE.get(name, name)


def _container_path(value: str, root: Path) -> str:
    """Translate workspace paths in arguments without exposing host paths."""
    host_root = str(root)
    variants = {host_root, host_root.replace("\\", "/"), host_root.replace("/", "\\")}
    translated = value
    for variant in sorted(variants, key=len, reverse=True):
        pattern = re.compile(re.escape(variant) + r"(?=$|[\\/])", re.IGNORECASE if os.name == "nt" else 0)
        translated = pattern.sub("/workspace", translated)
    return translated.replace("\\", "/") if translated.startswith("/workspace") else translated


def build_workspace_docker_argv(
    argv: list[str], workspace: Path | str, cwd: Path | str | None = None, *,
    image: str | None = None, container_name: str | None = None,
    limits: SandboxLimits = SandboxLimits(timeout_seconds=600, memory_mb=2048, cpus=2.0, pids=128),
    interactive: bool = False, env: dict[str, str] | None = None,
    read_only_workspace: bool = False,
) -> list[str]:
    """Create a fail-closed Docker command with exactly one writable host mount."""
    if not isinstance(argv, list) or not argv or len(argv) > 64 or not all(isinstance(arg, str) and arg for arg in argv):
        raise ValueError("Sandbox argv must contain 1 to 64 non-empty strings.")
    if not 1 <= limits.timeout_seconds <= 3600 or not 64 <= limits.memory_mb <= 8192 or not 0.1 <= limits.cpus <= 16 or not 1 <= limits.pids <= 1024:
        raise ValueError("Sandbox resource limits are outside the allowed range.")
    root = Path(workspace).expanduser().resolve(strict=True)
    workdir = Path(cwd or root).expanduser().resolve(strict=True)
    if not root.is_dir() or not workdir.is_dir() or (workdir != root and root not in workdir.parents):
        raise ValueError("Sandbox working directory must be inside the approved workspace.")
    source = str(root).replace("\\", "/")
    if "," in source or "\n" in source or "\r" in source:
        raise ValueError("Workspace path contains characters unsupported by Docker bind mounts.")
    selected_image, executable = _container_image(argv, image)
    name = container_name or f"smara-{uuid.uuid4().hex[:20]}"
    if not re.fullmatch(r"smara-[a-f0-9]{20}", name):
        raise ValueError("Sandbox container name is invalid.")
    docker = _docker_executable()
    state = docker_engine_status()
    if not state.get("engine_available"):
        raise RuntimeError("Docker Engine is not reachable; terminal execution is blocked without the sandbox.")
    if not state.get("linux_containers"):
        raise RuntimeError("Smara's workspace sandbox requires Docker Desktop in Linux-container mode.")
    image_check = subprocess.run(
        [docker, "image", "inspect", "--format", "{{.Id}}", selected_image],
        capture_output=True, text=True, timeout=15, check=False,
    )
    if image_check.returncode:
        raise RuntimeError(
            f"Sandbox image '{selected_image}' is not present locally. "
            + ("Run 'smara sandbox build' to prepare the coding tools." if selected_image == DEFAULT_CODING_IMAGE
               else f"Run 'docker pull {selected_image}' or configure a locally built image before using this tool.")
        )
    image_id = image_check.stdout.strip()
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", image_id):
        raise RuntimeError("Docker returned an invalid sandbox image identity.")

    relative_workdir = workdir.relative_to(root).as_posix()
    container_workdir = "/workspace" if relative_workdir == "." else f"/workspace/{relative_workdir}"
    rewritten = [_container_path(arg, root) for arg in argv[1:]]
    safe_env = {key: value for key, value in (env or {}).items() if key.upper() in _SANDBOX_ENV and isinstance(value, str)}
    safe_env.setdefault("HOME", "/tmp")
    safe_env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    safe_env.update({"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "safe.directory", "GIT_CONFIG_VALUE_0": "*"})
    if (root / "src").is_dir():
        safe_env.setdefault("PYTHONPATH", "/workspace/src")

    command = [
        docker, "run", "--rm", "--init", "--pull=never", "--name", name,
        "--label", "com.smara.workspace-sandbox=true",
        "--network", "none", "--read-only",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
        "--pids-limit", str(limits.pids), "--memory", f"{limits.memory_mb}m",
        "--cpus", str(limits.cpus),
        "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=256m,mode=1777",
        "--mount", f"type=bind,source={source},target=/workspace" + (",readonly" if read_only_workspace else ""),
        "--workdir", container_workdir, "--user", "1000:1000",
    ]
    if interactive:
        command.append("--interactive")
    for key, value in sorted(safe_env.items()):
        command.extend(["--env", f"{key}={value}"])
    # The deadline lives inside the container, so a disconnected Desktop or a
    # crashed Docker client cannot leave the command running indefinitely.
    command.extend(["--entrypoint", "sh", image_id, "-c",
                    f'exec timeout -s TERM -k 2 {int(limits.timeout_seconds)} "$@"',
                    "smara-deadline", executable, *rewritten])
    return command


def container_name_from_argv(argv: list[str] | tuple[str, ...]) -> str | None:
    try:
        index = list(argv).index("--name")
        name = list(argv)[index + 1]
    except (ValueError, IndexError):
        return None
    return name if re.fullmatch(r"smara-[a-f0-9]{20}", name) else None


def stop_workspace_container(name: str | None, *, timeout: int = 8) -> bool:
    if not name or not re.fullmatch(r"smara-[a-f0-9]{20}", name):
        return False
    docker = shutil.which("docker")
    if not docker:
        return False
    try:
        result = subprocess.run([docker, "stop", "--time", "1", name], capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def run_workspace_command(
    argv: list[str], workspace: Path | str, cwd: Path | str | None = None, *,
    image: str | None = None, timeout: int = 60,
    env: dict[str, str] | None = None, read_only_workspace: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run one command in the workspace-only Docker sandbox, stopping it on every exit path."""
    if not 1 <= int(timeout) <= 3600:
        raise ValueError("Sandbox timeout must be between 1 and 3600 seconds.")
    name = f"smara-{uuid.uuid4().hex[:20]}"
    command = build_workspace_docker_argv(
        argv, workspace, cwd, image=image, container_name=name, env=env,
        read_only_workspace=read_only_workspace,
        limits=SandboxLimits(timeout_seconds=int(timeout), memory_mb=2048, cpus=2.0, pids=128),
    )
    try:
        return subprocess.run(
            command, cwd=str(Path(workspace).resolve()), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout + 5, check=False,
        )
    except subprocess.TimeoutExpired:
        stop_workspace_container(name)
        raise
    finally:
        stop_workspace_container(name)


def wsl_command(command: str, config: WSLConfig = WSLConfig()) -> list[str]:
    """Build a non-login WSL command with explicit distribution selection."""
    if not command.strip():
        raise ValueError("WSL command cannot be empty.")
    distro = str(config.distribution).strip()
    if not distro or any(ch in distro for ch in "\r\n;&|`") or len(distro) > 64:
        raise ValueError("WSL distribution is invalid")
    return ["wsl.exe", "--distribution", distro, "--exec", "bash", "-lc", command]


def wsl_available(distribution: str = "Ubuntu") -> bool:
    """Probe WSL without mutating the host or starting a long-lived shell."""
    if platform.system().lower() != "windows" or shutil.which("wsl.exe") is None:
        return False
    try:
        result = subprocess.run(["wsl.exe", "--distribution", distribution, "--exec", "true"],
                                capture_output=True, text=True, timeout=8, env={"PATH": os.environ.get("PATH", "")}, check=False)
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def run_wsl(command: str, config: WSLConfig = WSLConfig()) -> str:
    if not 1 <= config.timeout_seconds <= 600:
        raise ValueError("WSL timeout is outside allowed range")
    completed = subprocess.run(wsl_command(command, config), capture_output=True, text=True,
                               timeout=config.timeout_seconds, env={"PATH": os.environ.get("PATH", "")}, check=False)
    output = (completed.stdout + completed.stderr)[-20_000:]
    if completed.returncode:
        raise RuntimeError(f"WSL exited with code {completed.returncode}: {output}")
    return output


def backend_status() -> dict[str, dict[str, object]]:
    docker = docker_engine_status()
    expected_images = {label: _container_image([executable])[0] for label, executable in {
        "python": "python", "pytest": "pytest", "shell": "sh", "bash": "bash", "powershell": "pwsh",
        "git": "git", "node": "node", "rust": "cargo", "go": "go",
    }.items()}
    available_images = {}
    if docker.get("engine_available") and docker.get("linux_containers"):
        executable = shutil.which("docker")
        for label, image in expected_images.items():
            result = subprocess.run([executable, "image", "inspect", image], capture_output=True, text=True, timeout=15, check=False)
            available_images[label] = result.returncode == 0
    return {
        "docker": {
            **docker,
            "available": bool(docker.get("engine_available") and docker.get("linux_containers")),
            "workspace_sandbox": bool(docker.get("engine_available") and docker.get("linux_containers")),
            "images_present": available_images,
            "expected_images_present": bool(available_images) and all(available_images.values()),
            "tool_readiness_note": "Image presence is checked; required binaries and project dependencies depend on each image's contents.",
            "isolation": "Linux container; only approved workspace mounted writable; network disabled; root filesystem read-only; CPU, memory and process limits",
        },
        "wsl": {"available": wsl_available(), "workspace_sandbox": False,
                "isolation": "WSL2 runtime only; not used as Smara's workspace/network sandbox"},
    }


def docker_command(command: str, limits: SandboxLimits = SandboxLimits()) -> list[str]:
    if not command.strip():
        raise ValueError("Sandbox command cannot be empty.")
    if not 1 <= limits.timeout_seconds <= 600 or not 64 <= limits.memory_mb <= 2048:
        raise ValueError("Sandbox limits are outside the allowed range.")
    return [
        "docker", "run", "--rm", "--network", "none", "--read-only",
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m", "--pids-limit", str(limits.pids),
        "--memory", f"{limits.memory_mb}m", "--cpus", str(limits.cpus),
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "python:3.12-alpine", "sh", "-lc", command,
    ]


def run(command: str, limits: SandboxLimits = SandboxLimits()) -> str:
    """Run a bounded process and return bounded output; never pass environment."""
    completed = subprocess.run(
        docker_command(command, limits), capture_output=True, text=True, timeout=limits.timeout_seconds,
        env={}, check=False,
    )
    output = (completed.stdout + completed.stderr)[-20_000:]
    if completed.returncode:
        raise RuntimeError(f"Sandbox exited with code {completed.returncode}: {output}")
    return output


async def run_remote(base_url: str, token: str, command: str, limits: SandboxLimits = SandboxLimits()) -> str:
    """Call a separately isolated sandbox service; never send Smara secrets."""
    if not base_url or not token:
        raise RuntimeError("Sandbox service is not configured.")
    if not command.strip():
        raise ValueError("Sandbox command cannot be empty.")
    async with httpx.AsyncClient(timeout=limits.timeout_seconds + 5, follow_redirects=False) as client:
        result = await client.post(
            f"{base_url.rstrip('/')}/v1/run",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            json={"command": command, "timeout_seconds": limits.timeout_seconds, "memory_mb": limits.memory_mb, "cpus": limits.cpus, "pids": limits.pids},
        )
        result.raise_for_status()
        data = result.json()
    output = data.get("output") if isinstance(data, dict) else None
    if not isinstance(output, str):
        raise RuntimeError("Sandbox service returned an invalid result.")
    if data.get("ok") is False:
        raise RuntimeError(f"Sandbox service rejected the command: {output[-2_000:]}")
    return output[-20_000:]
