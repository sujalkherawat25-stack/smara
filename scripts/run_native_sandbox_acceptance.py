"""One bounded, non-elevated Windows write-boundary probe in scratch folders.

Does not provision sandbox accounts, request UAC, use paid inference, or certify
network/junction/hardlink protection. Both write targets are disposable files.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
import os
from pathlib import Path
import shutil
import subprocess
import uuid

from smara.native_runtime import native_binary


@contextmanager
def inherited_acl_scratch(build: Path):
    # Python's Windows TemporaryDirectory uses a protected 0700 DACL. This
    # probe models an ordinary selected project folder with inherited ACLs,
    # not a private parent folder inaccessible to the restricted token.
    scratch = (build / ("native-sandbox-" + uuid.uuid4().hex)).resolve()
    assert scratch.is_relative_to(build.resolve()) and scratch.name.startswith("native-sandbox-")
    scratch.mkdir()
    try:
        yield scratch
    finally:
        # Only this exact newly created, validated test directory is removed.
        shutil.rmtree(scratch)


def main() -> int:
    if os.name != "nt":
        raise SystemExit("This probe tests the copied Windows sandbox only")
    root = Path(__file__).resolve().parents[1]
    with inherited_acl_scratch(root / "build") as scratch:
        workspace = scratch / "workspace"
        workspace.mkdir()
        outside = scratch / "outside"
        outside.mkdir()
        home = scratch / "runtime-home"
        home.mkdir()
        inside_file = workspace / "allowed-proof.txt"
        outside_file = outside / "blocked-proof.txt"
        # All paths are explicit descendants of this newly created scratch root.
        assert inside_file.is_relative_to(scratch) and outside_file.is_relative_to(scratch)
        def literal(path): return "'" + str(path).replace("'", "''") + "'"
        command = "$ErrorActionPreference='Stop'; Set-Content -LiteralPath " + literal(inside_file) + " -Value 'inside'; try { Set-Content -LiteralPath " + literal(outside_file) + " -Value 'outside'; Write-Output 'OUTSIDE_WRITE_ALLOWED' } catch { Write-Output 'OUTSIDE_WRITE_BLOCKED' }"
        shell = str(Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe")
        environment = dict(os.environ, CODEX_HOME=str(home))
        result = subprocess.run([str(native_binary()), "-c", 'windows.sandbox="unelevated"',
            "-c", "features.prefer_mxc=false", "sandbox", "--permission-profile", ":workspace", "-C", str(workspace), "--", shell, "-NoProfile", "-NonInteractive", "-Command", command],
            cwd=workspace, env=environment, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=45,
            creationflags=subprocess.CREATE_NO_WINDOW)
        inside_ok = inside_file.is_file() and inside_file.read_text().strip() == "inside"
        outside_blocked = not outside_file.exists() and "OUTSIDE_WRITE_BLOCKED" in result.stdout
        passed = result.returncode == 0 and inside_ok and outside_blocked
        print(json.dumps({"status": "passed" if passed else "failed", "sandbox": "windows-unelevated", "exit_code": result.returncode,
            "workspace_write_succeeded": inside_ok, "outside_workspace_write_blocked": outside_blocked,
            "stdout": result.stdout, "stderr": result.stderr[-2000:], "paid_inference": False, "elevated_setup": False,
            "network_or_path_escape_certification": False}))
        return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
