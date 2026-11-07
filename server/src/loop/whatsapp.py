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


def from_shop(tenant, text: str) -> str:
    """On the shared Smara number, start every customer message with the shop's name so the customer always knows who
    is writing. A shop with its own number needs no prefix: the number itself is the shop."""
    if getattr(tenant, "wa_phone_number_id", None) or not getattr(tenant, "name", ""):
        return text
    return text if text.lstrip().lstrip("*").lower().startswith(tenant.name.lower()) else f"*{tenant.name}*\n{text}"


def reminder_params(settings, o: dict, tenant) -> list[str]:
    """Template variables in the order the approved template expects (WA_REMINDER_PARAMS)."""
    from src.loop.upi import rupees
    values = {"customer": o["canonical_name"], "shop": getattr(tenant, "name", "") or "your supplier",
              "invoice": o["invoice_ref"], "amount": rupees(o["amount_paise"]), "due": f"{o['due_date']:%d %b}"}
    order = [k.strip() for k in str(getattr(settings, "wa_reminder_params", "") or "customer,invoice,amount,due").split(",")]
    return [values[k] for k in order if k in values]


def shop_choice_message(to: str, options: list[dict], routing_id: str) -> dict:
    """Ask a customer who deals with several Smara shops which one their message is about."""
    buttons = [{"type": "reply", "reply": {"id": f"shop:{o['tenant_id']}:{routing_id}", "title": o["name"][:20]}}
               for o in options[:3]]
    return {"messaging_product": "whatsapp", "to": to, "type": "interactive", "interactive": {
        "type": "button", "body": {"text": "Namaste! Aapka message kis business ke liye hai? / Which business is your message for?"},
        "action": {"buttons": buttons}}}


def text_message(to: str, text: str) -> dict:
    return {"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": text}}


class WhatsAppClient:
    """Sends payloads. Owner-bound messages only in Phase 1; debtor-bound
    sends must go through WAITING_OWNER_APPROVAL (Phase 2)."""

    def __init__(self, phone_number_id: str, access_token: str, client: httpx.AsyncClient | None = None,
                 token_for=None):
        self._default_number = phone_number_id
        self._token_for = token_for          # async (number_id) -> the shop's own token, or None to use ours
        self._http = client or httpx.AsyncClient(
            headers={"Authorization": f"Bearer {access_token}"}, timeout=15
        )

    async def _headers(self, number_id: str | None) -> dict:
        if number_id and number_id != self._default_number and self._token_for:
            tok = await self._token_for(number_id)
            if tok:
                return {"Authorization": f"Bearer {tok}"}
        return {}

    async def send(self, payload: dict, *, number_id: str | None = None) -> str | None:
        """`number_id` picks the sending WhatsApp number (per-tenant). Returns Meta's message id.

        A 200 only means Meta accepted it; delivery failures arrive later as status receipts."""
        resp = await self._http.post(f"{GRAPH}/{number_id or self._default_number}/messages", json=payload,
                                     headers=await self._headers(number_id))
        resp.raise_for_status()
        return ((resp.json().get("messages") or [{}])[0]).get("id")

    async def upload_media(self, data: bytes, mime: str, filename: str, *, number_id: str | None = None) -> str:
        """Upload a file to WhatsApp and return its media id (used to send it as a document)."""
        resp = await self._http.post(f"{GRAPH}/{number_id or self._default_number}/media",
                                     data={"messaging_product": "whatsapp", "type": mime},
                                     files={"file": (filename, data, mime)}, headers=await self._headers(number_id))
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
