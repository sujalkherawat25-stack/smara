import hashlib
import hmac
import time

import jwt
import pytest
from fastapi import HTTPException

from smara import api
from smara.config import Settings


def test_production_requires_signed_gateway(monkeypatch):
    monkeypatch.setattr(api, "settings", Settings(dev_mode=False, gateway_signing_secret="test-secret"))
    now = str(int(time.time()))
    signature = hmac.new(b"test-secret", f"{now}.acct_1".encode(), hashlib.sha256).hexdigest()
    assert api.account_id("acct_1", now, signature) == "acct_1"
    with pytest.raises(HTTPException) as error:
        api.account_id("acct_2", now, signature)
    assert error.value.status_code == 401


def test_production_accepts_short_lived_smara_web_bridge(monkeypatch):
    secret = "bridge-secret-for-tests-32-bytes!"
    monkeypatch.setattr(api, "settings", Settings(dev_mode=False, control_bridge_secret=secret))
    token = jwt.encode(
        {
            "sub": "acct_1",
            "iat": int(time.time()),
            "exp": int(time.time()) + 60,
            "aud": "smara-control",
            "iss": "ai.syntarus.com",
        },
        secret,
        algorithm="HS256",
    )
    assert api.account_id(authorization=f"Bearer {token}") == "acct_1"


def test_production_rejects_wrong_bridge_audience(monkeypatch):
    secret = "bridge-secret-for-tests-32-bytes!"
    monkeypatch.setattr(api, "settings", Settings(dev_mode=False, control_bridge_secret=secret))
    token = jwt.encode(
        {
            "sub": "acct_1",
            "iat": int(time.time()),
            "exp": int(time.time()) + 60,
            "aud": "another-service",
            "iss": "ai.syntarus.com",
        },
        secret,
        algorithm="HS256",
    )
    with pytest.raises(HTTPException) as error:
        api.account_id(authorization=f"Bearer {token}")
    assert error.value.status_code == 401


def test_hosted_personal_integrations_are_local_only_by_default(monkeypatch):
    monkeypatch.setattr(api, "settings", Settings(hosted_user_integrations_enabled=False))
    with pytest.raises(HTTPException) as error:
        api._require_hosted_user_integrations()
    assert error.value.status_code == 409
    assert "local-only" in str(error.value.detail)
