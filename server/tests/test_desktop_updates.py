"""Smara Desktop's updater: a newer signed release is offered, nothing else is, and only release files can be downloaded."""
import json
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.desktop_updates import router


def client(tmp_path, latest=None, base="https://ai.example.com/smara"):
    app = FastAPI()
    app.include_router(router)
    app.state.settings = SimpleNamespace(desktop_releases_dir=str(tmp_path), public_base_url=base)
    if latest:
        (tmp_path / latest["file"]).write_bytes(b"MZ installer")
        (tmp_path / "latest.json").write_text(json.dumps(latest))
    return TestClient(app)


RELEASE = {"version": "0.2.7", "file": "Smara Desktop_0.2.7_x64-setup.exe", "signature": "c2ln", "notes": "Fixes"}


def test_an_older_app_is_offered_the_signed_release(tmp_path):
    c = client(tmp_path, RELEASE)
    got = c.get("/desktop/update/windows/x86_64/0.2.6")
    assert got.status_code == 200
    body = got.json()
    assert body["version"] == "0.2.7" and body["signature"] == "c2ln"
    assert body["url"] == "https://ai.example.com/smara/desktop/download/Smara%20Desktop_0.2.7_x64-setup.exe"
    assert c.get("/desktop/download/Smara Desktop_0.2.7_x64-setup.exe").content == b"MZ installer"


def test_a_current_or_newer_app_gets_nothing(tmp_path):
    c = client(tmp_path, RELEASE)
    for version in ("0.2.7", "0.3.0", "0.10.0"):
        assert c.get(f"/desktop/update/windows/x86_64/{version}").status_code == 204
    assert c.get("/desktop/update/darwin/aarch64/0.1.0").status_code == 204
    assert c.get("/desktop/update/windows/x86_64/not-a-version").status_code == 204


def test_no_release_or_a_broken_manifest_offers_nothing(tmp_path):
    assert client(tmp_path).get("/desktop/update/windows/x86_64/0.1.0").status_code == 204
    (tmp_path / "latest.json").write_text(json.dumps({**RELEASE, "file": "../../etc/passwd"}))
    assert client(tmp_path).get("/desktop/update/windows/x86_64/0.1.0").status_code == 204
    (tmp_path / "latest.json").write_text(json.dumps({**RELEASE, "signature": ""}))
    assert client(tmp_path).get("/desktop/update/windows/x86_64/0.1.0").status_code == 204


def test_only_release_installers_can_be_downloaded(tmp_path):
    c = client(tmp_path, RELEASE)
    (tmp_path / "latest.json").write_text("{}")
    for name in ("latest.json", "..%2F..%2Fetc%2Fpasswd", "x.dll", "missing.exe"):
        assert c.get(f"/desktop/download/{name}").status_code == 404
