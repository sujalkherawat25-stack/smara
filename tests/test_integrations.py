from pathlib import Path
import asyncio
import json

from cryptography.fernet import Fernet
import httpx

from smara.integrations import IntegrationExecutor
from smara.store import TaskStore
from smara.vault import SecretVault


def test_integration_policy_and_idempotency_are_account_scoped(tmp_path: Path):
    store = TaskStore(str(tmp_path / "smara.db"))
    store.configure_integration("acct_1", "gmail", display_name="Work Gmail", policy="observe", granted_scopes=["gmail.readonly"], health="healthy")
    blocked = store.request_integration_action("acct_1", "gmail", "gmail.send", "Send report to client", "send-report-001")
    assert blocked["risk"] == "external"
    assert blocked["status"] == "blocked"
    same = store.request_integration_action("acct_1", "gmail", "gmail.send", "Different text must not duplicate", "send-report-001")
    assert same["id"] == blocked["id"]
    assert store.integrations("acct_2") == []


def test_trusted_does_not_bypass_external_approval_before_executor_exists(tmp_path: Path):
    store = TaskStore(str(tmp_path / "smara.db"))
    store.configure_integration("acct_1", "github", display_name="Repo", policy="trusted", granted_scopes=["repo"], health="healthy")
    action = store.request_integration_action("acct_1", "github", "github.push", "Push a reviewed commit", "push-commit-001")
    assert action["status"] == "awaiting_approval"


def test_approved_action_is_leased_once_and_credential_is_ciphertext(tmp_path: Path):
    store = TaskStore(str(tmp_path / "smara.db"))
    store.configure_integration("acct_1", "telegram", display_name="Alerts", policy="assisted", granted_scopes=[], health="not_connected")
    vault = SecretVault(Fernet.generate_key().decode())
    store.store_integration_credential("acct_1", "telegram", "bot_token", vault.encrypt("plain-bot-token"))
    action = store.request_integration_action("acct_1", "telegram", "telegram.send", "Send status", "telegram-send-0001", {"chat_id": "1", "text": "Done"})
    approved = store.decide_integration_action("acct_1", action["id"], True, "Okay")
    assert approved["status"] == "approved"
    claimed = store.claim_integration_action("worker-a")
    assert claimed and claimed["id"] == action["id"]
    assert store.claim_integration_action("worker-b") is None
    encrypted = store.encrypted_integration_credential(claimed["connection_id"])
    assert "plain-bot-token" not in encrypted["encrypted_secret"]
    assert vault.decrypt(encrypted["encrypted_secret"]) == "plain-bot-token"
    store.complete_integration_action(claimed["id"], "worker-a", result="Provider accepted")
    assert store.integration_actions("acct_1")[0]["status"] == "completed"


def test_gmail_adapter_sends_only_the_approved_payload():
    received: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        received["url"] = str(request.url)
        received["body"] = json.loads(request.content)
        assert request.headers["authorization"] == "Bearer test-token"
        return httpx.Response(200, json={"id": "message_1"})

    async def run() -> str:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            return await IntegrationExecutor(http).execute("gmail", "gmail.send", {"to": "person@example.com", "subject": "Approved", "text": "Only this text"}, json.dumps({"access_token": "test-token"}))

    assert asyncio.run(run()) == "Gmail message accepted by provider."
    assert received["url"] == "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
    assert "raw" in received["body"]
