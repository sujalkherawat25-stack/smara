import hashlib
import time

import jwt
import pytest
from fastapi import HTTPException

from smara import api
from smara.config import Settings
from smara.store import TaskStore


def test_cli_pairing_is_single_use_and_stores_only_hash(tmp_path):
    store = TaskStore(str(tmp_path / "smara.db"))
    pairing = store.create_cli_pairing("acct_1", "Test laptop")
    assert pairing["code"].startswith("smara_")
    with store._connect() as connection:
        row = connection.execute("SELECT * FROM cli_pairings").fetchone()
    assert row["code_hash"] == hashlib.sha256(pairing["code"].encode()).hexdigest()
    assert row["code_hash"] != pairing["code"]
    assert store.consume_cli_pairing(pairing["code"])["account_id"] == "acct_1"
    with pytest.raises(KeyError):
        store.consume_cli_pairing(pairing["code"])


def test_cli_device_authorization_requires_browser_approval(tmp_path):
    store = TaskStore(str(tmp_path / "smara.db"))
    request = store.create_cli_device_request("Test laptop")
    assert store.poll_cli_device(request["device_code"])["status"] == "pending"
    approved = store.authorize_cli_device(request["device_code"], "acct_1")
    assert approved["account_id"] == "acct_1"
    result = store.poll_cli_device(request["device_code"])
    assert result == {"status": "approved", "account_id": "acct_1", "name": "Test laptop"}
    assert store.poll_cli_device(request["device_code"])["status"] == "used"


def test_cli_device_request_cannot_be_approved_twice(tmp_path):
    store = TaskStore(str(tmp_path / "smara.db"))
    request = store.create_cli_device_request("Test laptop")
    store.authorize_cli_device(request["device_code"], "acct_1")
    with pytest.raises(KeyError):
        store.authorize_cli_device(request["device_code"], "acct_2")


def test_account_id_rejects_legacy_unregistered_cli_jwt(monkeypatch):
    secret = "cli-secret-for-tests-at-least-32-bytes!"
    monkeypatch.setattr(api, "settings", Settings(dev_mode=False, cli_token_secret=secret))
    now = int(time.time())
    token = jwt.encode({"sub": "acct_1", "name": "test", "jti": "cli_1", "iat": now, "exp": now + 60, "aud": "smara-cli", "iss": "smara-api"}, secret, algorithm="HS256")
    with pytest.raises(HTTPException) as legacy_error:
        api.account_id(authorization=f"Bearer {token}")
    assert legacy_error.value.status_code == 401
    assert "renewed" in legacy_error.value.detail
    wrong_audience = jwt.encode({"sub": "acct_1", "jti": "cli_1", "iat": now, "exp": now + 60, "aud": "smara-control", "iss": "ai.syntarus.com"}, secret, algorithm="HS256")
    with pytest.raises(HTTPException) as error:
        api.account_id(authorization=f"Bearer {wrong_audience}")
    assert error.value.status_code == 401


def test_account_id_accepts_only_active_registered_cli_device(monkeypatch, tmp_path):
    secret = "cli-secret-for-tests-at-least-32-bytes!"
    test_store = TaskStore(str(tmp_path / "smara.db"))
    monkeypatch.setattr(api, "settings", Settings(dev_mode=False, cli_token_secret=secret))
    monkeypatch.setattr(api, "store", test_store)
    now = int(time.time())
    jti = "cli_registered_1"
    test_store.register_cli_device("acct_1", "Test laptop", jti, "2999-01-01T00:00:00+00:00")
    token = jwt.encode({"sub": "acct_1", "name": "Test laptop", "jti": jti, "device_registered": True, "iat": now, "exp": now + 60, "aud": "smara-cli", "iss": "smara-api"}, secret, algorithm="HS256")
    assert api.account_id(authorization=f"Bearer {token}") == "acct_1"
    test_store.revoke_cli_jti("acct_1", jti)
    with pytest.raises(HTTPException) as revoked_error:
        api.account_id(authorization=f"Bearer {token}")
    assert revoked_error.value.status_code == 401
