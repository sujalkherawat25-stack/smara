# Local coding and terminal sandbox

Smara's agent terminal/Python tools, test runner, DAG command nodes, worker
commands and Desktop terminal/sandbox actions run through
Docker on the local machine. If Docker is unavailable, is in Windows-container
mode, or the selected image is missing, execution fails closed; Smara does not
fall back to running the command directly on the host.

Workspace execution uses one writable bind mount for the approved workspace.
The container root filesystem is read-only, network access is disabled, Linux
capabilities are dropped, and CPU, memory, process count, and execution time
are bounded. Application credentials and the host `PATH` are not copied into
the container. A command can still modify or delete files inside its approved
workspace, so choose that workspace deliberately.

## Images and dependencies

Images are never pulled automatically. This avoids a hidden network fetch and
keeps image selection under the machine owner's control. Prepare the shared
Python/pytest/Git/Bash/Node/npm image explicitly:

```powershell
smara --workspace C:\Users\sujal\smara sandbox build
smara sandbox status
```

This creates `smara-coding:local` from a minimal build context containing only
the fixed Dockerfile and public registry dependency requirements. Source code,
Git history and credentials are not copied into the image. The explicit build
uses networking to obtain base images and dependencies; task execution does not.
For another Python project, choose its workspace when building, preferably
with a separate `--image` tag and the corresponding runtime image override.

PowerShell, Rust/Cargo and Go retain separate optional images. Each selected image must
already exist locally and contain the requested executable. Image presence
does not guarantee that a binary or the project's dependencies are installed.

Override image references with the matching environment variable:

| Tool | Environment variable |
| --- | --- |
| Any unmapped tool | `SMARA_SANDBOX_IMAGE` |
| Python | `SMARA_SANDBOX_PYTHON_IMAGE` |
| pytest | `SMARA_SANDBOX_PYTEST_IMAGE` |
| POSIX `sh` | `SMARA_SANDBOX_SHELL_IMAGE` |
| Bash | `SMARA_SANDBOX_BASH_IMAGE` |
| PowerShell | `SMARA_SANDBOX_POWERSHELL_IMAGE` |
| Git | `SMARA_SANDBOX_GIT_IMAGE` |
| Node and npm | `SMARA_SANDBOX_NODE_IMAGE` |
| Rust and Cargo | `SMARA_SANDBOX_RUST_IMAGE` |
| Go | `SMARA_SANDBOX_GO_IMAGE` |

For example, after reviewing a trusted image, pull it explicitly and point
Smara at it:

```powershell
docker pull <trusted-image>
$env:SMARA_SANDBOX_PYTEST_IMAGE = "<trusted-image>"
```

Since the container has no network, package installation during a command
(such as `pip install`, `npm install`, or downloading a browser) will not work.
Use a reviewed image with the required toolchain and dependencies preinstalled.
The `smara backends` and `smara sandbox status` commands report Docker Engine and expected image presence;
it does not inspect every image for every binary or project dependency.

## Local verification

Run `python -m pytest tests/test_docker_workspace_sandbox.py -q` with a reachable
Docker Linux engine, the coding image and the fixture images available. The tests check
workspace-only writes, parent-path escape prevention, sibling-file read
isolation, disabled outbound networking, and environment filtering.

The command deadline is enforced inside the container, independently of UI
polling or the client process. Session-owned process logs and exit receipts are
also persisted. Tester/auditor commands receive a read-only workspace mount.

Coder and reviewer snapshots are standalone local Git clones of a committed
baseline, with independent Git metadata. Reviewers receive the exact captured
coder patch, including new files; their patch hashes must match. Uncommitted
caller changes are not copied. Modified or uninspectable snapshots are preserved.
Swarm/delegation remains opt-in (`SMARA_ENABLE_DELEGATION=1`) while the maintained
real coding quality gate is failing. The parent agent's automatic delegation
tool remains disabled; these worker improvements are not a claim of live
multi-agent qualification.
