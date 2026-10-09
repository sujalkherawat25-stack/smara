"""Actual frozen metadata/DPAPI acceptance in isolated synthetic state only."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executor", type=Path, required=True)
    parser.add_argument("--cli", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    fixture = root / "build" / ("native-settings-check-" + uuid.uuid4().hex)
    data = fixture / "data"
    first, second = fixture / "first स्मारा 😀", fixture / "second project"
    for directory in (data, first, second): directory.mkdir(parents=True)
    original = json.dumps({"workspace": str(first), "history": "synthetic-history-sentinel"}).encode()
    (data / "desktop.json").write_bytes(original)
    env = {key: value for key, value in os.environ.items() if key.upper() in {
        "SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "LOCALAPPDATA", "APPDATA", "COMSPEC", "ALLUSERSPROFILE"}}
    env.update(SMARA_DESKTOP_STATE=str(data / "desktop.json"), SMARA_DESKTOP_CREDENTIALS=str(data / "credentials.json"))
    def call(operation, **values):
        result = subprocess.run([str(args.executor.resolve()), "--native-manage"], input=json.dumps({"operation": operation, **values}),
            capture_output=True, text=True, encoding="utf-8", env=env, timeout=30)
        if result.returncode:
            raise RuntimeError("Frozen settings operation failed: " + operation)
        return json.loads(result.stdout)["result"]
    checks = {}
    initial = call("bootstrap")
    checks["legacy_project_metadata_imported"] = initial["preferences"]["projects"][0]["workspace"] == str(first.resolve())
    call("add_project", workspace=str(second), label="Second")
    call("select_project", workspace=str(first))
    synthetic_key = "synthetic-local-only-not-a-service-credential"
    profile = {"id": "fixture", "label": "Offline fixture", "provider": "local", "model": "synthetic-protocol",
               "base_url": "http://127.0.0.1:9/v1", "auth_header": "authorization", "api_key": synthetic_key}
    saved = call("save_model", profile=profile)
    call("select_model", id="fixture")
    vault_before_edit = (data / "credentials.json").read_bytes()
    profile.update(label="Updated offline fixture", api_key="")
    call("save_model", profile=profile)
    checks["blank_key_edit_preserves_dpapi_vault"] = (data / "credentials.json").read_bytes() == vault_before_edit
    checks["no_key_in_model_metadata_or_response"] = synthetic_key not in (data / "native-settings.json").read_text(encoding="utf-8") + json.dumps(saved)
    checks["encrypted_vault_not_plaintext"] = synthetic_key.encode() not in vault_before_edit
    # Internal resolver is not exposed to the renderer; test only our synthetic key.
    checks["real_dpapi_roundtrip"] = call("resolve_credential", name="SMARA_MODEL_FIXTURE_API_KEY")["secret"] == synthetic_key
    cli = subprocess.run([str(args.cli.resolve()), "settings"], capture_output=True, text=True, encoding="utf-8", env=env, timeout=30)
    try:
        value = json.loads(cli.stdout) if cli.returncode == 0 else {}
    except ValueError:
        value = {}
    checks["CLI_and_Desktop_share_native_configuration"] = cli.returncode == 0 and value["preferences"]["active_model"] == "fixture" and len(value["preferences"]["projects"]) == 2
    checks["CLI_settings_do_not_return_secret"] = synthetic_key not in cli.stdout
    checks["original_history_and_preferences_untouched"] = (data / "desktop.json").read_bytes() == original
    report = {"status": "passed" if all(checks.values()) else "failed", "scope": "frozen synthetic metadata and DPAPI, not GUI grants or model quality",
              "fixture": str(fixture), "checks": checks, "model_or_provider_requests": 0, "personal_state_modified": False}
    (fixture / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
