"""Bounded execution recipe for untrusted code.

This module is deliberately not exposed as an HTTP endpoint. A future approved
executor may call it after policy validation; it never mounts host files,
inherits production secrets, or grants network access.
"""
from __future__ import annotations

import subprocess
import shutil
import os
import platform
import httpx
from dataclasses import dataclass


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
    return {
        "docker": {"available": shutil.which("docker") is not None, "isolation": "network-none,cap-drop-all,pids-limited"},
        "wsl": {"available": wsl_available(), "isolation": "WSL2 distribution boundary"},
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
