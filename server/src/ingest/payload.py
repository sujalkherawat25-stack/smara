import hashlib
import hmac
from dataclasses import dataclass

from src.config import normalize_phone


MEDIA_KINDS = ("audio", "image", "document", "video", "sticker")


@dataclass(frozen=True)
class InboundMessage:
    wamid: str
    sender: str            # digits only
    kind: str              # text | audio | image | interactive | ...
    text: str = ""
    media_id: str | None = None
    button_id: str | None = None
    caption: str = ""
    filename: str = ""
    number_id: str = ""   # our WhatsApp number that received it
    channel: str = "whatsapp"   # whatsapp | telegram
    chat_id: int | None = None  # telegram chat
    reply_to: str = ""          # the message of ours they replied to (WhatsApp context.id), if any
    mime: str = ""             # media type as WhatsApp reports it
    link_tenant_id: str = ""    # channel "wa_link": the shop whose own SIM received it (server/wa_link)
    profile_name: str = ""     # the name the sender's own profile shows (WhatsApp contacts, Telegram first name)
    size: int = 0              # bytes the channel reported for a file or voice note (0 = not known until downloaded)

    @property
    def evidence_text(self) -> str:
        """Text to store as evidence; media is a placeholder until STT/OCR (Phase 2)."""
        if self.text:
            return self.text
        if self.caption:
            return f"{self.caption} [{self.kind} message media_id={self.media_id}]"
        return f"[{self.kind} message media_id={self.media_id}]"


def verify_signature(app_secret: str, raw_body: bytes, header: str | None) -> bool:
    if not app_secret or not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


def _line(value) -> str:
    return " ".join(str(value or "").split())[:120]


def shared_text(m: dict) -> str:
    """A shared contact or location, or a tapped template button, as the words a person would have typed. '' for others."""
    kind = m.get("type")
    if kind == "contacts":
        cards = []
        for c in (m.get("contacts") or [])[:10]:
            numbers = ", ".join(_line(p.get("phone")) for p in (c.get("phones") or [])[:3] if p.get("phone"))
            cards.append(f"{_line((c.get('name') or {}).get('formatted_name')) or 'no name'}: {numbers or 'no number'}")
        return "[Shared contact] " + "; ".join(cards) if cards else ""
    if kind == "location":
        place = m.get("location") or {}
        bits = [_line(place.get("name")), _line(place.get("address"))]
        lat, lng = place.get("latitude"), place.get("longitude")
        if isinstance(lat, (int, float)) and isinstance(lng, (int, float)):
            bits.append(f"https://maps.google.com/?q={lat},{lng}")
        return "[Shared location] " + ", ".join(b for b in bits if b) if any(bits) else ""
    if kind == "button":
        return _line((m.get("button") or {}).get("text"))
    return ""


def parse_messages(payload: dict) -> list[InboundMessage]:
    out: list[InboundMessage] = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            number_id = (value.get("metadata") or {}).get("phone_number_id", "")
            names = {c.get("wa_id", ""): ((c.get("profile") or {}).get("name") or "") for c in value.get("contacts", [])}
            for m in value.get("messages", []):
                kind = m.get("type", "unknown")
                msg = InboundMessage(
                    wamid=m.get("id", ""),
                    sender=normalize_phone(m.get("from", "")),
                    kind=kind,
                    text=(m.get("text") or {}).get("body", "") or shared_text(m),
                    media_id=(m.get(kind) or {}).get("id") if kind in MEDIA_KINDS else None,
                    caption=(m.get(kind) or {}).get("caption", "") if kind in ("image", "document", "video") else "",
                    filename=(m.get(kind) or {}).get("filename", "") if kind == "document" else "",
                    number_id=number_id,
                    mime=(m.get(kind) or {}).get("mime_type", "") if kind in MEDIA_KINDS else "",
                    button_id=(((i := m.get("interactive") or {}).get("button_reply") or i.get("list_reply")) or {}).get("id"),
                    reply_to=(m.get("context") or {}).get("id", ""),
                    profile_name=names.get(m.get("from", ""), ""),
                )
                if msg.wamid and msg.sender:
                    out.append(msg)
    return out


@dataclass(frozen=True)
class StatusUpdate:
    msg_id: str
    status: str            # sent | delivered | read | failed
    recipient: str
    error_code: int | None = None
    error_title: str = ""


def parse_statuses(payload: dict) -> list[StatusUpdate]:
    out: list[StatusUpdate] = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            for st in change.get("value", {}).get("statuses", []):
                err = (st.get("errors") or [{}])[0]
                if st.get("id") and st.get("status"):
                    out.append(StatusUpdate(st["id"], st["status"], normalize_phone(st.get("recipient_id", "")),
                                            err.get("code"), err.get("title") or err.get("message") or ""))
    return out
