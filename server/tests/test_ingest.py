import hashlib
import hmac
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.bridge.continuum import make_source_id
from src.config import Settings
from src.ingest.classify import classify_claim
from src.ingest.payload import parse_messages, verify_signature
from src.ingest.webhook import router
from src.loop.whatsapp import owner_approval_card

SECRET = "s3cret"


def sign(body: bytes) -> str:
    return "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


def wa_payload(**msg) -> dict:
    base = {"id": "wamid.1", "from": "+91 98765 43210", "type": "text", "text": {"body": "hi"}}
    return {"entry": [{"changes": [{"value": {"messages": [{**base, **msg}]}}]}]}


def test_signature():
    body = b'{"a":1}'
    assert verify_signature(SECRET, body, sign(body))
    assert not verify_signature(SECRET, body, "sha256=00")
    assert not verify_signature(SECRET, body, None)
    assert not verify_signature("", body, sign(body))


def test_parse_text_audio_button():
    (m,) = parse_messages(wa_payload())
    assert (m.sender, m.text) == ("919876543210", "hi")
    (a,) = parse_messages(wa_payload(type="audio", audio={"id": "M1"}, text=None))
    assert a.media_id == "M1" and "M1" in a.evidence_text
    (b,) = parse_messages(wa_payload(
        type="interactive", text=None,
        interactive={"button_reply": {"id": "approve:abc", "title": "Approve"}}))
    assert b.button_id == "approve:abc"


def test_source_id_deterministic_and_valid():
    a = make_source_id("t", "w1", "x")
    assert a == make_source_id("t", "w1", "x") != make_source_id("t", "w2", "x")
    assert a.replace("_", "").isalnum()


def test_classify():
    assert classify_claim("UTR 123456789012 bhej diya") == "UTR_PROOF"
    assert classify_claim("thoda discount kar do") == "DISCOUNT_CLAIM"
    assert classify_claim("kal tak bhej dunga") == "PROMISE_DATE_CHANGE"
    assert classify_claim("ok") is None


def test_owner_card_buttons():
    card = owner_approval_card("91", adjustment_id="A1", party_name="Ravi", invoice_ref="INV-1",
                               adjustment_type="UTR_PROOF", evidence="paid")
    ids = [b["reply"]["id"] for b in card["interactive"]["action"]["buttons"]]
    assert ids == ["approve:A1", "reject:A1"]


def make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.state.settings = Settings(wa_verify_token="vt", wa_app_secret=SECRET)
    return app


def test_webhook_verify_and_rejects_unsigned():
    c = TestClient(make_app())
    ok = c.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "vt", "hub.challenge": "42"})
    assert ok.status_code == 200 and ok.text == "42"
    assert c.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "no", "hub.challenge": "1"}).status_code == 403
    assert c.post("/webhook", content=b"{}").status_code == 401


def test_webhook_signed_empty_payload_ok():
    c = TestClient(make_app())
    body = json.dumps({"entry": []}).encode()
    r = c.post("/webhook", content=body, headers={"x-hub-signature-256": sign(body)})
    assert r.status_code == 200
