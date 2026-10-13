"""Offline protocol fixtures. No mailbox, OAuth account, or real send is used."""
import base64
from email import message_from_bytes, policy
import json
import time
from types import SimpleNamespace
import httpx
import pytest
from smara import native_outreach as mail, native_management as management
from smara.native_profiles import read_object
from smara.native_tools import NativeTools


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("SMARA_DESKTOP_STATE", str(tmp_path / "desktop.json"))
    monkeypatch.setenv("SMARA_DESKTOP_CREDENTIALS", str(tmp_path / "credentials.json"))
    for module in (mail, management): monkeypatch.setattr(module, "protect", lambda s: "synthetic-protected:" + s)
    monkeypatch.setattr(mail, "unprotect", lambda s: s.removeprefix("synthetic-protected:"))
    token = {"email": "sender@example.org", "scope": " ".join(mail.SCOPES), "access_token": "synthetic-access-token", "expires_at": time.time() + 3600}
    monkeypatch.setattr(mail, "account", lambda: token)
    monkeypatch.setattr(mail, "config", lambda: {"enabled": True, "client_id": "synthetic.apps.googleusercontent.com", "allowlist": ["hello@example.org"], "daily_limit": 5})
    sources = {"synthetic-source-id": {"final_url": "https://example.org/contact", "text": "Example builds synthetic tools. Contact hello@example.org to discuss a pilot."}}
    draft = {"company": "Example", "company_domain": "example.org", "to": "hello@example.org", "subject": "Synthetic pilot discussion", "text": "Hello — this is a synthetic draft, not an actual outreach campaign.",
             "evidence": [{"source_id": "synthetic-source-id", "claim": "Example publishes this contact address", "quote": sources["synthetic-source-id"]["text"]}]}
    return SimpleNamespace(root=tmp_path, store=mail.OutreachStore(tmp_path / "outreach.sqlite"), draft=draft, sources=sources, token=token)


def test_draft_is_local_protected_and_cannot_guess_addresses(setup):
    row = setup.store.create(setup.root, setup.draft, setup.sources)
    assert row["state"] == "draft" and row["draft"]["review_required"]
    assert not row["draft"]["semantic_support_verified"]
    with setup.store.connect() as db:
        assert db.execute("SELECT payload FROM outreach").fetchone()[0].startswith("synthetic-protected:")
    assert setup.store.create(setup.root, setup.draft, setup.sources)["id"] == row["id"]
    with pytest.raises(ValueError, match="already"): setup.store.create(setup.root, {**setup.draft, "text": "Different content"}, setup.sources)
    for bad in ({**setup.draft, "to": "guessed@example.org"}, {**setup.draft, "subject": "subject\nBcc: other@example.org"}):
        with pytest.raises(ValueError): setup.store.create(setup.root, bad, setup.sources)


def test_send_requires_review_cohort_and_one_atomic_attempt(setup, monkeypatch):
    row = setup.store.create(setup.root, setup.draft, setup.sources)
    calls = []
    def transport(request):
        calls.append(request)
        body = json.loads(request.content)
        message = message_from_bytes(base64.urlsafe_b64decode(body["raw"]), policy=policy.default)
        assert message["To"] == setup.draft["to"] and message["Subject"] == setup.draft["subject"]
        assert message.get_content().strip() == setup.draft["text"]
        return httpx.Response(200, json={"id": "synthetic-id", "threadId": "synthetic-thread"})
    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        with pytest.raises(ValueError): setup.store.send(row["id"], setup.root, "old-content", client)
        original_config = mail.config()
        monkeypatch.setattr(mail, "config", lambda: {**original_config, "allowlist": []})
        with pytest.raises(ValueError, match="cohort"): setup.store.send(row["id"], setup.root, row["content_hash"], client)
        monkeypatch.setattr(mail, "config", lambda: original_config)
        result = setup.store.send(row["id"], setup.root, row["content_hash"], client)
        assert result["state"] == "accepted" and result["message_id"] == "synthetic-id"
        with pytest.raises(ValueError, match="already"): setup.store.send(row["id"], setup.root, row["content_hash"], client)
    assert len(calls) == 1


def test_uncertain_send_never_resends_and_sent_reconciliation_needs_matching_headers(setup):
    row = setup.store.create(setup.root, setup.draft, setup.sources)
    def failed(request): raise httpx.ReadTimeout("Synthetic uncertain response", request=request)
    with httpx.Client(transport=httpx.MockTransport(failed)) as client:
        assert setup.store.send(row["id"], setup.root, row["content_hash"], client)["state"] == "unknown"
        with pytest.raises(ValueError): setup.store.send(row["id"], setup.root, row["content_hash"], client)
    def reconcile(request):
        if request.url.path.endswith("/messages"): return httpx.Response(200, json={"messages": [{"id": "synthetic-id"}]})
        return httpx.Response(200, json={"labelIds": ["SENT"], "threadId": "synthetic-thread", "payload": {"headers": [
            {"name": "Message-ID", "value": row["draft"]["message_id_header"]},
            {"name": "To", "value": setup.draft["to"]}, {"name": "From", "value": setup.token["email"]}]}})
    with httpx.Client(transport=httpx.MockTransport(reconcile)) as client:
        result = setup.store.reconcile(row["id"], setup.root, client)
    assert result["state"] == "sent_verified" and "delivery/reply is not established" in result["detail"]


def test_suppression_and_scope_stop_unapproved_dispatch(setup):
    row = setup.store.create(setup.root, setup.draft, setup.sources)
    setup.store.suppress(row["id"], setup.root)
    with pytest.raises(ValueError): setup.store.send(row["id"], setup.root, row["content_hash"])
    elsewhere = setup.root / "elsewhere"; elsewhere.mkdir()
    with pytest.raises(ValueError): setup.store.get(row["id"], elsewhere)


def test_reader_exposes_only_approved_local_drafts_never_a_send_tool(setup, monkeypatch):
    tools = NativeTools(setup.root, standard_tools=True)
    names = {tool["name"] for tool in tools.tool_specs}
    assert "outreach_draft" in names and not any("send" in name for name in names)
    spec = next(tool for tool in tools.tool_specs if tool["name"] == "outreach_draft")
    assert not spec["annotations"]["readOnlyHint"]
    monkeypatch.setattr(mail, "config", lambda: {"enabled": False})
    assert "outreach_draft" not in {tool["name"] for tool in NativeTools(setup.root).tool_specs}


def test_synthetic_oauth_uses_loopback_pkce_and_protected_vault_not_model(setup, monkeypatch):
    from smara import native_mail_oauth as oauth
    from urllib.parse import urlsplit, parse_qs, urlencode
    import threading
    import hashlib
    import urllib.request
    requests = []
    def transport(request):
        requests.append(request)
        if request.url.host == "oauth2.googleapis.com":
            data = parse_qs(request.content.decode())
            assert data["grant_type"] == ["authorization_code"]
            assert data["redirect_uri"][0].startswith("http://127.0.0.1:")
            assert len(data["code_verifier"][0]) >= 43
            return httpx.Response(200, json={"access_token": "synthetic-access", "refresh_token": "synthetic-refresh", "scope": " ".join(mail.SCOPES), "expires_in": 3600})
        return httpx.Response(200, json={"emailAddress": "sender@example.org"})
    def opener(url):
        query = parse_qs(urlsplit(url).query)
        assert query["code_challenge_method"] == ["S256"]
        assert set(query["scope"][0].split()) == mail.SCOPES
        def callback():
            bad = query["redirect_uri"][0] + "?" + urlencode({"state": "wrong", "code": "synthetic-code"})
            try: urllib.request.urlopen(bad, timeout=3)
            except Exception: pass
            good = query["redirect_uri"][0] + "?" + urlencode({"state": query["state"][0], "code": "synthetic-code"})
            with urllib.request.urlopen(good, timeout=3) as response: assert b"No email" in response.read()
        threading.Thread(target=callback, daemon=True).start()
        return True
    monkeypatch.setattr(oauth, "config", mail.config)
    monkeypatch.setattr(oauth, "resolve_credential", lambda *_args, **_kwargs: "synthetic-client-secret")
    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        assert oauth.authorize(timeout=5, client=client, opener=opener)["connected"]
    vault = read_object(management.vault_path())
    assert vault[mail.TOKEN_ALIAS]["protected"].startswith("synthetic-protected:")
    assert len(requests) == 2 and not any("/send" in str(r.url) for r in requests)


def test_real_windows_dpapi_protects_draft_body(setup, monkeypatch):
    import os
    if os.name != "nt": pytest.skip("Real Windows DPAPI fixture")
    from smara.native_profiles import protect, unprotect
    monkeypatch.setattr(mail, "protect", protect)
    monkeypatch.setattr(mail, "unprotect", unprotect)
    row = setup.store.create(setup.root, setup.draft, setup.sources)
    assert setup.store.get(row["id"], setup.root)["draft"]["text"] == setup.draft["text"]
    assert setup.draft["text"].encode() not in setup.store.path.read_bytes()


def test_edit_invalidates_older_content_review_and_never_changes_recipient(setup):
    row = setup.store.create(setup.root, setup.draft, setup.sources)
    edited = setup.store.edit(row["id"], setup.root, row["content_hash"], "Updated synthetic subject", "Updated synthetic body")
    assert edited["content_hash"] != row["content_hash"] and edited["draft"]["to"] == row["draft"]["to"]
    with pytest.raises(ValueError, match="Review"): setup.store.send(row["id"], setup.root, row["content_hash"])
    with pytest.raises(ValueError): setup.store.edit(row["id"], setup.root, row["content_hash"], "Stale", "Stale")
