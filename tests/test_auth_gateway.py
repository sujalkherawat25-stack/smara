import hashlib
import hmac
import time

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
