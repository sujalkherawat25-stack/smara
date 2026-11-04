"""WhatsApp Cloud API sender + Thread A (Owner) interactive card builders."""
import httpx

GRAPH = "https://graph.facebook.com/v20.0"


def owner_approval_card(
    to: str, *, adjustment_id: str, party_name: str, invoice_ref: str,
    adjustment_type: str, evidence: str, proposal: str = "",
) -> dict:
    """Interactive [Approve]/[Reject] card. Button ids carry the adjustment id."""
    body = (
        f"{party_name} wrote about invoice {invoice_ref} ({adjustment_type}):\n"
        f"\"{evidence[:300]}\"\n"
        + (f"\nProposed: {proposal}\n" if proposal else "")
        + "\nUnverified. Apply this?"
    )
    return {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": body[:1024]},
            "action": {"buttons": [
                {"type": "reply", "reply": {"id": f"approve:{adjustment_id}", "title": "Approve"}},
                {"type": "reply", "reply": {"id": f"reject:{adjustment_id}", "title": "Reject"}},
            ]},
        },
    }


def text_message(to: str, text: str) -> dict:
    return {"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": text}}


class WhatsAppClient:
    """Sends payloads. Owner-bound messages only in Phase 1; debtor-bound
    sends must go through WAITING_OWNER_APPROVAL (Phase 2)."""

    def __init__(self, phone_number_id: str, access_token: str, client: httpx.AsyncClient | None = None):
        self._default_number = phone_number_id
        self._http = client or httpx.AsyncClient(
            headers={"Authorization": f"Bearer {access_token}"}, timeout=15
        )

    async def send(self, payload: dict, *, number_id: str | None = None) -> str | None:
        """`number_id` picks the sending WhatsApp number (per-tenant). Returns Meta's message id.

        A 200 only means Meta accepted it; delivery failures arrive later as status receipts."""
        resp = await self._http.post(f"{GRAPH}/{number_id or self._default_number}/messages", json=payload)
        resp.raise_for_status()
        return ((resp.json().get("messages") or [{}])[0]).get("id")

    async def upload_media(self, data: bytes, mime: str, filename: str, *, number_id: str | None = None) -> str:
        """Upload a file to WhatsApp and return its media id (used to send it as a document)."""
        resp = await self._http.post(f"{GRAPH}/{number_id or self._default_number}/media",
                                     data={"messaging_product": "whatsapp", "type": mime},
                                     files={"file": (filename, data, mime)})
        resp.raise_for_status()
        return resp.json()["id"]

    async def aclose(self) -> None:
        await self._http.aclose()


def template_message(to: str, name: str, lang: str, params: list[str]) -> dict:
    """Pre-approved template: the only way to open a conversation outside the 24h window."""
    return {"messaging_product": "whatsapp", "to": to, "type": "template", "template": {
        "name": name, "language": {"code": lang},
        "components": [{"type": "body", "parameters": [{"type": "text", "text": p} for p in params]}]}}


def document_message(to: str, media_id: str, filename: str, caption: str = "") -> dict:
    doc = {"id": media_id, "filename": filename}
    if caption:
        doc["caption"] = caption[:1000]
    return {"messaging_product": "whatsapp", "to": to, "type": "document", "document": doc}
