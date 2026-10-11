"""Launch a source or installed Desktop with synthetic Git data and no model/key.

Visual QA only. Real Git actions are available; model/terminal execution is not
simulated. Leaves its uniquely named fixture for inspection, never installs.
"""
from pathlib import Path
import argparse
import json
import os
import subprocess
import tempfile


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installed", action="store_true", help="Test the installed binary with isolated synthetic state")
    options = parser.parse_args()
    executable = (Path(os.environ["LOCALAPPDATA"]) / "Smara Desktop/smara-desktop.exe" if options.installed
                  else root / "apps/desktop/src-tauri/target/debug/smara-desktop.exe")
    if not executable.is_file():
        parser.error("The requested Desktop binary is not available")
    fixture = Path(tempfile.mkdtemp(prefix="changes-desktop-", dir=root / "build"))
    project = fixture / "project"
    project.mkdir()
    def git(*args):
        subprocess.run(["git", *args], cwd=project, check=True, capture_output=True)
    git("init")
    git("config", "user.name", "Offline GUI fixture")
    git("config", "user.email", "fixture@example.invalid")
    git("config", "core.autocrlf", "false")
    (project / "example.py").write_bytes(b"def multiply(a, b):\n    return a + b\n")
    git("add", "example.py")
    git("commit", "-m", "Synthetic baseline")
    (project / "example.py").write_bytes(b"def multiply(a, b):\n    return a * b\n")
    state = fixture / "desktop.json"
    state.write_text(json.dumps({"workspace": str(project), "local_model_profiles": []}), encoding="utf-8")
    (fixture / "native-settings.json").write_text(json.dumps({"projects": [{"workspace": str(project), "label": "OFFLINE Git review fixture"}], "workspace": str(project)}), encoding="utf-8")
    companion = executable.parent / "resources/smara-desktop.exe" if options.installed else fixture / "no-packaged-helper.exe"
    environment = dict(os.environ, SMARA_DESKTOP_STATE=str(state), SMARA_DESKTOP_CREDENTIALS=str(fixture / "empty-credentials.json"),
                       SMARA_DESKTOP_EXECUTABLE=str(companion), SMARA_DESKTOP_DATA_DIR=str(fixture))
    process = subprocess.Popen([str(executable)], env=environment)
    print(json.dumps({"pid": process.pid, "fixture": str(fixture), "workspace": str(project), "model_requests": 0,
                      "installed_binary": options.installed}), flush=True)
    raise SystemExit(process.wait())  # Keep the isolated launch environment alive.


if __name__ == "__main__":
    main()
