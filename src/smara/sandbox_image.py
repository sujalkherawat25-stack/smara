"""Prepare the local coding toolchain using a minimal, source-free build context."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tempfile
import tomllib
from pathlib import Path

from .sandbox import DEFAULT_CODING_IMAGE, _docker_executable, docker_engine_status


DOCKERFILE = """FROM node:22-bookworm-slim AS node
FROM python:3.12-slim
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm && \\
    ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx
RUN apt-get update && apt-get install -y --no-install-recommends bash git ca-certificates coreutils && \\
    rm -rf /var/lib/apt/lists/*
COPY requirements.txt /tmp/requirements.txt
RUN python -m pip install --no-cache-dir -r /tmp/requirements.txt && rm /tmp/requirements.txt
ENV PYTHONDONTWRITEBYTECODE=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /workspace
LABEL com.smara.coding-toolchain=true
"""


def coding_requirements(workspace: Path) -> list[str]:
    metadata = workspace / "pyproject.toml"
    requirements = ["pytest>=8,<9"]
    if metadata.is_file():
        value = tomllib.loads(metadata.read_text(encoding="utf-8"))
        requirements.extend(value.get("project", {}).get("dependencies", []))
    for requirement in requirements:
        if (not isinstance(requirement, str) or not re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]*", requirement)
                or any(marker in requirement for marker in ("\n", "\r", "://", "@", "/", "\\", "--"))):
            raise ValueError("Sandbox image dependencies must be registry package requirements, without local paths or credentialed URLs.")
    return list(dict.fromkeys(requirements))


def build_coding_image(workspace: Path, image: str = DEFAULT_CODING_IMAGE) -> dict:
    root = workspace.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Sandbox project must be a directory")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/:-]{0,250}", image):
        raise ValueError("Invalid coding image tag")
    status = docker_engine_status()
    if not status.get("engine_available") or not status.get("linux_containers"):
        raise RuntimeError("Start Docker Desktop in Linux-container mode before building the coding image.")
    requirements = coding_requirements(root)
    # Only public dependency metadata and this fixed Dockerfile reach Docker.
    # No source code, Git history, credentials, or unrelated project files are copied.
    with tempfile.TemporaryDirectory(prefix="smara-toolchain-") as directory:
        context = Path(directory)
        (context / "Dockerfile").write_text(DOCKERFILE, encoding="utf-8")
        (context / "requirements.txt").write_text("\n".join(requirements) + "\n", encoding="utf-8")
        subprocess.run([_docker_executable(), "build", "--tag", image, str(context)], check=True, timeout=900)
    identity = subprocess.check_output([_docker_executable(), "image", "inspect", "--format", "{{.Id}}", image], text=True).strip()
    return {"image": image, "image_id": identity, "requirements_sha256": hashlib.sha256(json.dumps(requirements).encode()).hexdigest(),
            "tools": ["python3", "pytest", "git", "bash", "node", "npm"], "source_files_copied": 0}
