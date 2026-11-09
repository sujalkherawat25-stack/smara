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


def parse_messages(payload: dict) -> list[InboundMessage]:
    out: list[InboundMessage] = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            number_id = (value.get("metadata") or {}).get("phone_number_id", "")
            for m in value.get("messages", []):
                kind = m.get("type", "unknown")
                msg = InboundMessage(
                    wamid=m.get("id", ""),
                    sender=normalize_phone(m.get("from", "")),
                    kind=kind,
                    text=(m.get("text") or {}).get("body", ""),
                    media_id=(m.get(kind) or {}).get("id") if kind in MEDIA_KINDS else None,
                    caption=(m.get(kind) or {}).get("caption", "") if kind in ("image", "document", "video") else "",
                    filename=(m.get(kind) or {}).get("filename", "") if kind == "document" else "",
                    number_id=number_id,
                    mime=(m.get(kind) or {}).get("mime_type", "") if kind in MEDIA_KINDS else "",
                    button_id=((m.get("interactive") or {}).get("button_reply") or {}).get("id"),
                    reply_to=(m.get("context") or {}).get("id", ""),
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
