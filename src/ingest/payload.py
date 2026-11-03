import hashlib
import hmac
from dataclasses import dataclass

from src.config import normalize_phone


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
                    media_id=(m.get(kind) or {}).get("id") if kind in ("audio", "image", "document") else None,
                    caption=(m.get(kind) or {}).get("caption", "") if kind in ("image", "document") else "",
                    filename=(m.get(kind) or {}).get("filename", "") if kind == "document" else "",
                    number_id=number_id,
                    button_id=((m.get("interactive") or {}).get("button_reply") or {}).get("id"),
                )
                if msg.wamid and msg.sender:
                    out.append(msg)
    return out
