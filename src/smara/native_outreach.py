"""Research-backed local drafts and reviewed Gmail dispatch, NOT an agent loop.

The agent may prepare a draft; it has no send tool. Sending is a user-directed
management action bound to exact content, an approved recipient cohort and a
daily cap. Unknown outcomes are reconciled, never automatically resent.
"""
from contextlib import contextmanager
import base64
from email.message import EmailMessage
from email.utils import parseaddr
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
from urllib.parse import urlsplit
import uuid
import httpx

from .native_profiles import protect, unprotect, state_path, read_object, resolve_credential
from .native_goals import workspace_key

TOKEN_ALIAS = "SMARA_GMAIL_OAUTH"
CLIENT_ALIAS = "SMARA_GMAIL_CLIENT_SECRET"
API = "https://gmail.googleapis.com/gmail/v1/users/me"
SCOPES = {"https://www.googleapis.com/auth/gmail.send", "https://www.googleapis.com/auth/gmail.readonly"}


def address(value):
    if (not isinstance(value, str) or len(value) > 254 or
            not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,63}", value)):
        raise ValueError("Provide one explicit email address, without display name or extra recipients")
    return value.lower()


def config():
    value = read_object(state_path().parent / "native-settings.json").get("outreach", {})
    return {"enabled": value.get("enabled") is True, "client_id": str(value.get("client_id") or ""),
            "allowlist": value.get("allowlist", []), "daily_limit": value.get("daily_limit", 5)}


def account():
    raw = resolve_credential(TOKEN_ALIAS, allow_environment=False)
    if not raw: raise ValueError("Connect Gmail before sending; drafts remain local")
    value = json.loads(raw)
    if not isinstance(value, dict) or not isinstance(value.get("scope"), str) or not SCOPES.issubset(set(value["scope"].split())):
        raise ValueError("Gmail needs the reviewed sending and read-back scopes; reconnect")
    value["email"] = address(value.get("email"))
    if not isinstance(value.get("access_token"), str) or not value["access_token"]:
        raise ValueError("Gmail credentials are incomplete; reconnect")
    return value


def status():
    try:
        value = account()
        sender = address(value["email"])
    except (OSError, ValueError, KeyError, RuntimeError):
        sender = ""
    return {**config(), "provider": "gmail", "connected": bool(sender), "sender": sender,
            "network_tested": False, "detail": "Local readiness only. Sending always requires exact-content review."}


class OutreachStore:
    def __init__(self, path=None):
        self.path = Path(path or state_path().parent / "native-outreach.sqlite")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS outreach (
                    id TEXT PRIMARY KEY, workspace TEXT NOT NULL, recipient_hash TEXT NOT NULL,
                    payload TEXT NOT NULL, content_hash TEXT NOT NULL, state TEXT NOT NULL,
                    created REAL NOT NULL, attempted REAL, message_id TEXT, thread_id TEXT,
                    sender_hash TEXT, detail TEXT NOT NULL DEFAULT '');
                CREATE UNIQUE INDEX IF NOT EXISTS outreach_recipient_idx ON outreach(workspace,recipient_hash);
            """)
            db.execute("UPDATE outreach SET state='unknown',detail='Dispatch interrupted; reconcile before any further action' WHERE state='sending' AND attempted<?", (time.time() - 120,))

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10); db.row_factory = sqlite3.Row
        try:
            with db: yield db
        finally: db.close()

    def get(self, identity, workspace):
        with self.connect() as db:
            row = db.execute("SELECT * FROM outreach WHERE id=? AND workspace=?", (identity, workspace_key(workspace))).fetchone()
        if row is None: raise ValueError("Draft not found in this project")
        result = dict(row)
        result["draft"] = json.loads(unprotect(result.pop("payload")))
        # A content approval is bound to the protected immutable payload.
        digest = hashlib.sha256(json.dumps(result["draft"], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        if digest != result["content_hash"]: raise ValueError("Draft content changed; create and review a new draft")
        return result

    def list(self, workspace):
        with self.connect() as db:
            ids = [r[0] for r in db.execute("SELECT id FROM outreach WHERE workspace=? ORDER BY created DESC LIMIT 20", (workspace_key(workspace),))]
        return [self.get(identity, workspace) for identity in ids]

    def create(self, workspace, draft, sources):
        if not config()["enabled"]: raise ValueError("Enable local outreach drafts in Settings → Email first")
        if not isinstance(draft, dict) or set(draft) != {"company", "company_domain", "to", "subject", "text", "evidence"}:
            raise ValueError("Draft requires company, company_domain, to, subject, text and evidence")
        recipient = address(draft["to"])
        for field, limit in (("company", 200), ("company_domain", 253), ("subject", 200), ("text", 8000)):
            if not isinstance(draft[field], str) or not draft[field].strip() or len(draft[field]) > limit:
                raise ValueError("Draft text is missing or exceeds its field limit")
        if "\n" in draft["subject"] or "\r" in draft["subject"]:
            raise ValueError("Email subjects cannot contain newlines")
        domain = draft["company_domain"].lower()
        if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.[a-z]{2,63}", domain):
            raise ValueError("Provide the company's public website domain")
        evidence = draft["evidence"]
        if not isinstance(evidence, list) or not 1 <= len(evidence) <= 20:
            raise ValueError("Research-backed drafts need observed source evidence")
        verified, contact = [], False
        for item in evidence:
            if not isinstance(item, dict) or set(item) != {"source_id", "quote", "claim"}:
                raise ValueError("Each evidence entry needs source_id, quote and claim")
            record = sources.get(item["source_id"])
            quote, claim = item["quote"], item["claim"]
            if (not record or not isinstance(quote, str) or not 20 <= len(quote) <= 4000
                    or not isinstance(claim, str) or not 1 <= len(claim) <= 2000
                    or " ".join(quote.split()) not in " ".join(record["text"].split())):
                raise ValueError("Evidence must quote a page actually fetched in this reader session")
            url = record.get("final_url", "")
            parsed = urlsplit(url); host = (parsed.hostname or "").lower()
            if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
                raise ValueError("Outreach evidence requires public web pages, not private files")
            if (host == domain or host.endswith("." + domain)) and recipient in quote.lower(): contact = True
            verified.append({"url": url, "source_id": item["source_id"], "claim": claim, "quote": quote})
        if not contact: raise ValueError("The recipient must appear in an observed contact excerpt on the company's own website; do not guess addresses")
        identity = uuid.uuid4().hex
        payload = {**draft, "to": recipient, "company_domain": domain, "evidence": verified,
                   "message_id_header": "<smara-" + identity + "@smara.local>", "review_required": True,
                   "semantic_support_verified": False}
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        recipient_hash = hashlib.sha256(recipient.encode()).hexdigest()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT id FROM outreach WHERE workspace=? AND recipient_hash=?", (workspace_key(workspace), recipient_hash)).fetchone()
            if existing:
                previous = self.get(existing["id"], workspace)
                if all(previous["draft"].get(key) == payload.get(key) for key in ("company", "company_domain", "to", "subject", "text", "evidence")):
                    return {**previous, "existing_record": True}
                raise ValueError("This project already has a different draft or contact record for that recipient; review/edit it instead of creating a duplicate")
            db.execute("INSERT INTO outreach(id,workspace,recipient_hash,payload,content_hash,state,created) VALUES(?,?,?,?,?,'draft',?)",
                       (identity, workspace_key(workspace), recipient_hash, protect(json.dumps(payload)), digest, time.time()))
        return self.get(identity, workspace)

    def edit(self, identity, workspace, content_hash, subject, text):
        row = self.get(identity, workspace)
        if row["state"] != "draft" or row["content_hash"] != content_hash:
            raise ValueError("Only an unattempted draft of the reviewed revision can be edited")
        if (not isinstance(subject, str) or not subject.strip() or len(subject) > 200 or "\n" in subject or "\r" in subject
                or not isinstance(text, str) or not text.strip() or len(text) > 8000):
            raise ValueError("Provide a single-line subject and non-empty body within the draft limits")
        payload = {**row["draft"], "subject": subject, "text": text}
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        with self.connect() as db:
            changed = db.execute("UPDATE outreach SET payload=?,content_hash=? WHERE id=? AND state='draft' AND content_hash=?",
                                 (protect(json.dumps(payload)), digest, identity, content_hash))
            if changed.rowcount != 1: raise ValueError("Draft changed or dispatch started; refresh before editing")
        return self.get(identity, workspace)

    def suppress(self, identity, workspace):
        row = self.get(identity, workspace)
        if row["state"] in {"sending", "unknown"}: raise ValueError("Reconcile the uncertain dispatch before changing its record")
        with self.connect() as db:
            changed = db.execute("UPDATE outreach SET state='suppressed',detail='Do not contact' WHERE id=? AND state NOT IN ('sending','unknown')", (identity,))
            if changed.rowcount != 1: raise ValueError("Dispatch started; reconcile before changing its record")
        return self.get(identity, workspace)

    def send(self, identity, workspace, content_hash, client=None):
        row = self.get(identity, workspace)
        if row["content_hash"] != content_hash: raise ValueError("Review the exact current recipient and content before sending")
        options, credentials = config(), account()
        recipient = row["draft"]["to"]
        if not options["enabled"] or recipient not in options["allowlist"]:
            raise ValueError("Add this exact recipient to the approved cohort in Settings → Email")
        sender_hash = hashlib.sha256(credentials["email"].lower().encode()).hexdigest()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            attempts = db.execute("SELECT COUNT(*) FROM outreach WHERE sender_hash=? AND attempted>=?", (sender_hash, time.time() - 86400)).fetchone()[0]
            if attempts >= options["daily_limit"]: raise ValueError("Approved daily email allowance reached")
            changed = db.execute("UPDATE outreach SET state='sending',attempted=?,sender_hash=? WHERE id=? AND state='draft' AND content_hash=?",
                                 (time.time(), sender_hash, identity, content_hash))
            if changed.rowcount != 1: raise ValueError("This draft has already been attempted or suppressed; it will not be resent")
        owns = client is None
        client = client or httpx.Client(timeout=httpx.Timeout(10, connect=3), follow_redirects=False, trust_env=False)
        state, detail, message_id, thread_id = "unknown", "Dispatch outcome uncertain; reconcile, do not resend", None, None
        try:
            token = access_token(credentials, client)
            message = EmailMessage()
            message["From"], message["To"], message["Subject"] = credentials["email"], recipient, row["draft"]["subject"]
            message["Message-ID"] = row["draft"]["message_id_header"]
            message.set_content(row["draft"]["text"])
            raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
            response = client.post(API + "/messages/send", headers={"Authorization": "Bearer " + token}, json={"raw": raw})
            if response.status_code == 200:
                result = response.json()
                if not isinstance(result, dict): raise ValueError("Invalid Gmail dispatch response")
                message_id, thread_id = result.get("id"), result.get("threadId")
                if not isinstance(thread_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", thread_id): thread_id = None
                if isinstance(message_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,200}", message_id):
                    state, detail = "accepted", "Gmail accepted this message; recipient delivery is not verified"
            elif 400 <= response.status_code < 500:
                state, detail = "failed", "Gmail rejected this dispatch; no automatic retry"
        except (OSError, ValueError, httpx.HTTPError):
            pass  # Persist UNKNOWN below: never turn transport failure into success/retry.
        finally:
            if owns: client.close()
            with self.connect() as db:
                db.execute("UPDATE outreach SET state=?,detail=?,message_id=?,thread_id=? WHERE id=? AND state='sending'",
                           (state, detail, message_id, thread_id, identity))
        return self.get(identity, workspace)

    def reconcile(self, identity, workspace, client=None):
        row, credentials = self.get(identity, workspace), account()
        if row["state"] not in {"unknown", "accepted", "sent_verified"}:
            raise ValueError("Only accepted or uncertain dispatches can be reconciled")
        if row["sender_hash"] != hashlib.sha256(credentials["email"].lower().encode()).hexdigest():
            raise ValueError("Reconnect the original sending account before reconciliation")
        owns = client is None
        client = client or httpx.Client(timeout=httpx.Timeout(10, connect=3), follow_redirects=False, trust_env=False)
        try:
            headers = {"Authorization": "Bearer " + access_token(credentials, client)}
            response = client.get(API + "/messages", headers=headers, params={"q": "in:sent rfc822msgid:" + row["draft"]["message_id_header"], "maxResults": 2})
            response.raise_for_status(); matches = response.json().get("messages", [])
            if len(matches) != 1: return row  # Zero or ambiguous results are not proof of non-delivery.
            message_id = matches[0].get("id")
            if not isinstance(message_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", message_id): raise ValueError("Invalid Gmail message reference")
            response = client.get(API + "/messages/" + message_id, headers=headers, params={"format": "metadata"})
            response.raise_for_status(); result = response.json()
            metadata = {h["name"].lower(): h["value"] for h in result.get("payload", {}).get("headers", [])}
            if ("SENT" in result.get("labelIds", []) and metadata.get("message-id") == row["draft"]["message_id_header"]
                    and parseaddr(metadata.get("to", ""))[1].lower() == row["draft"]["to"]
                    and parseaddr(metadata.get("from", ""))[1].lower() == credentials["email"].lower()):
                with self.connect() as db:
                    db.execute("UPDATE outreach SET state='sent_verified',message_id=?,thread_id=?,detail='Verified in Gmail Sent; recipient delivery/reply is not established' WHERE id=? AND state IN ('unknown','accepted','sent_verified')",
                               (message_id, result.get("threadId"), identity))
            return self.get(identity, workspace)
        finally:
            if owns: client.close()


def access_token(credentials, client):
    if credentials.get("expires_at", 0) > time.time() + 60: return credentials["access_token"]
    options = config()
    response = client.post("https://oauth2.googleapis.com/token", data={"client_id": options["client_id"],
        "client_secret": resolve_credential(CLIENT_ALIAS, allow_environment=False), "refresh_token": credentials.get("refresh_token", ""), "grant_type": "refresh_token"})
    response.raise_for_status(); refreshed = response.json()
    if not isinstance(refreshed.get("access_token"), str) or not refreshed["access_token"]:
        raise ValueError("Gmail token refresh failed; reconnect")
    credentials = {**credentials, **refreshed, "expires_at": time.time() + int(refreshed.get("expires_in", 3600))}
    from .native_management import management_lock, put_credential
    with management_lock(): put_credential(TOKEN_ALIAS, json.dumps(credentials), "gmail")
    return credentials["access_token"]


def operate(request):
    from .native_management import preferences
    if not isinstance(request, dict) or set(request) - {"action", "workspace", "id", "content_hash", "subject", "text"}:
        raise ValueError("Invalid outreach management request")
    workspace = request.get("workspace") or preferences()["workspace"]
    if not isinstance(workspace, str) or not Path(workspace).is_absolute():
        raise ValueError("Choose an absolute registered project folder")
    if not any(workspace_key(p["workspace"]) == workspace_key(workspace) for p in preferences()["projects"]):
        raise ValueError("Choose a registered project for outreach")
    action = request.get("action", "list")
    if action in {"send", "edit", "reconcile", "suppress"} and (not isinstance(request.get("id"), str) or not re.fullmatch(r"[0-9a-f]{32}", request["id"])):
        raise ValueError("Choose one existing local draft record")
    store = OutreachStore()
    if action == "list": return {"status": status(), "drafts": store.list(workspace)}
    if action == "connect":
        from .native_mail_oauth import start
        return start()
    if action == "disconnect":
        from .native_management import management_lock, vault_path
        from .native_profiles import write_object
        from .native_mail_oauth import health_path
        with management_lock():
            records = read_object(vault_path()); records.pop(TOKEN_ALIAS, None)
            write_object(vault_path(), records)
            write_object(health_path(), {"state": "disconnected", "generation": uuid.uuid4().hex, "updated": time.time()})
        return {"detail": "Disconnected locally; local drafts remain. Revoke Google account access separately if desired."}
    if action == "edit": return store.edit(request.get("id"), workspace, request.get("content_hash"), request.get("subject"), request.get("text"))
    if action == "send": return store.send(request.get("id"), workspace, request.get("content_hash"))
    if action == "reconcile":
        try: return store.reconcile(request.get("id"), workspace)
        except httpx.HTTPError: raise ValueError("Gmail read-back is unavailable. The saved dispatch was retained and will not be resent.") from None
    if action == "suppress": return store.suppress(request.get("id"), workspace)
    raise ValueError("Unsupported outreach action; agents may draft but cannot send")
