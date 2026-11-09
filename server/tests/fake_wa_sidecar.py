"""An in-memory stand-in for the Baileys sidecar (server/wa_link). It answers the same HTTP API with the same status
codes, so the app's client and guardrails can be tested without WhatsApp. Tests move a shop's device through its
states with connect()/drop()/log_out(); the app's callbacks are exercised by posting to the internal endpoints."""
import json
import re
import time

import httpx

TOKEN = "fake-sidecar-token-0123456789abcdefghij"
_PATH = re.compile(r"^/sessions/([0-9a-f-]{36})(?:/(pair|send|disconnect))?$")


class FakeSidecar:
    def __init__(self, token: str = TOKEN):
        self.token = token
        self.sessions: dict[str, dict] = {}    # shop -> {"status", "phone"}
        self.sent: list[dict] = []             # every message the device was asked to send
        self.pairs: list[tuple[str, str | None]] = []
        self.down = False                      # True: connection refused (the service is not running)
        self._ids = 0

    # ---- the device, driven by the tests
    def connect(self, shop: str, phone: str = "919812345678") -> None:
        self.sessions[shop] = {"status": "connected", "phone": phone}

    def drop(self, shop: str) -> None:
        """The device is gone from memory (the sidecar restarted)."""
        self.sessions.pop(shop, None)

    # ---- the HTTP API
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _respond(self, status: int, body: dict) -> httpx.Response:
        return httpx.Response(status, json=body)

    def _handle(self, req: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("connection refused", request=req)
        if req.url.path == "/healthz":
            return self._respond(200, {"ok": True, "sessions": len(self.sessions)})
        if req.headers.get("authorization") != f"Bearer {self.token}":
            return self._respond(401, {"error": "unauthorized"})
        m = _PATH.match(req.url.path)
        if not m:
            return self._respond(404, {"error": "not found"})
        shop, action = m.group(1), m.group(2)
        if req.method == "GET" and action is None:
            s = self.sessions.get(shop)
            if s is None:
                return self._respond(200, {"status": "absent", "phone": "", "qr_svg": ""})
            qr = "<svg>fake-qr</svg>" if s["status"] == "pairing" else ""
            return self._respond(200, {"status": s["status"], "phone": s["phone"], "qr_svg": qr})
        body = json.loads(req.content or b"{}")
        if req.method == "POST" and action == "pair":
            self.pairs.append((shop, body.get("auth")))
            if body.get("auth") is None:
                self.sessions[shop] = {"status": "pairing", "phone": ""}
            else:
                self.sessions[shop] = {"status": "connecting", "phone": ""}
            return self._respond(200, {"status": self.sessions[shop]["status"], "phone": ""})
        if req.method == "POST" and action == "send":
            s = self.sessions.get(shop)
            if s is None or s["status"] != "connected":
                return self._respond(409, {"error": "not connected"})
            self._ids += 1
            self.sent.append({"shop": shop, "to": body["to"], "text": body["text"], "at": time.monotonic()})
            return self._respond(200, {"message_id": f"SIM-{self._ids}"})
        if req.method == "POST" and action == "disconnect":
            self.sessions.pop(shop, None)
            return self._respond(200, {"status": "disconnected", "phone": ""})
        return self._respond(404, {"error": "not found"})

    def sent_to(self, digits: str) -> list[dict]:
        return [m for m in self.sent if m["to"] == digits]
