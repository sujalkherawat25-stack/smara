from dataclasses import replace

from smara import auth
from smara.auth_store import AccountStore
from smara.config import Settings


def test_account_store_preserves_google_account_and_telegram_link(tmp_path):
    store = AccountStore(database_path=str(tmp_path / "accounts.db"))
    store.ensure_schema()
    account = store.upsert_google_account(
        google_sub="google-sub-1", email="owner@example.com", display_name="Owner", avatar_url=None
    )
    assert account["id"].startswith("acct_")
    again = store.upsert_google_account(
        google_sub="google-sub-1", email="owner@example.com", display_name="Owner Updated", avatar_url=None
    )
    assert again["id"] == account["id"]
    assert again["display_name"] == "Owner Updated"
    code = store.create_telegram_code(account["id"])
    linked = store.redeem_telegram_code(code["code"], 1234, 5678)
    assert linked == account["id"]
    assert store.telegram_account(1234)["id"] == account["id"]
    assert store.telegram_status(account["id"])["linked"] is True
    assert store.redeem_telegram_code(code["code"], 1234, 5678) is None


def test_native_session_is_revocable(tmp_path, monkeypatch):
    settings = replace(auth.settings, session_secret="unit-test-secret", accounts_database_url="", database_path=str(tmp_path / "accounts.db"))
    store = AccountStore(database_path=settings.database_path)
    store.ensure_schema()
    monkeypatch.setattr(auth, "settings", settings)
    monkeypatch.setattr(auth, "account_store", store)
    account = store.upsert_google_account(google_sub="google-sub-2", email="two@example.com", display_name="Two", avatar_url=None)
    token, _ = auth.issue_session(account["id"])
    assert auth.verify_session_cookie(token) == account["id"]
    store.delete_session(token and __import__("jwt").decode(token, settings.session_secret, algorithms=["HS256"], options={"verify_aud": False})["jti"])
    assert auth.verify_session_cookie(token) is None

