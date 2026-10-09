"""Exact NSIS payload identity; synthetic bytes exercise the checksum helper."""
import hashlib
import os
from pathlib import Path
import subprocess

import pytest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows installer helper")
MARKER = b"__TAURI_BUNDLE_TYPE_VAR_UNK"


def payload_hash(path: Path):
    helper = Path(__file__).resolve().parents[1] / "scripts/smara-install-validation.ps1"
    quote = lambda value: "'" + str(value).replace("'", "''") + "'"
    command = "$ErrorActionPreference='Stop'; . " + quote(helper) + "; Get-SmaraNsisPayloadHash -Path " + quote(path)
    return subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
                          capture_output=True, text=True, timeout=20)


def test_nsis_hash_accounts_only_for_bundle_marker(tmp_path):
    source = b"\x00synthetic PE fixture\xff" + MARKER + b"\x00unchanged payload"
    built = tmp_path / "built.exe"
    built.write_bytes(source)
    result = payload_hash(built)
    installed = source.replace(MARKER, b"__TAURI_BUNDLE_TYPE_VAR_NSS")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().lower() == hashlib.sha256(installed).hexdigest()
    assert result.stdout.strip().lower() != hashlib.sha256(installed + b"tampered").hexdigest()
    assert result.stdout.strip().lower() != hashlib.sha256(installed.replace(b"unchanged", b"modified!")).hexdigest()


@pytest.mark.parametrize("source", [b"no marker", MARKER + b"duplicate" + MARKER])
def test_nsis_hash_rejects_missing_or_ambiguous_marker(tmp_path, source):
    built = tmp_path / "built.exe"
    built.write_bytes(source)
    result = payload_hash(built)
    assert result.returncode != 0
    assert "exactly one" in result.stderr
