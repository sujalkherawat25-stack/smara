"""Durable local message gateway with at-least-once delivery semantics."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True)
class GatewayEvent:
    event_id: str
    channel: str
    sender: str
    text: str
    session_id: str
    idempotency_key: str
    created_at: float


class GatewayLedger:
    def __init__(self, path: Path | str):
        self.path = Path(path).expanduser().resolve(); self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS gateway_events (
              idempotency_key TEXT PRIMARY KEY, event_id TEXT NOT NULL, channel TEXT NOT NULL,
              sender TEXT NOT NULL, text TEXT NOT NULL, session_id TEXT NOT NULL,
              status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
              response TEXT, last_error TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL
            )""")

    def ingest(self, *, channel: str, sender: str, text: str, session_id: str | None = None, idempotency_key: str | None = None) -> GatewayEvent:
        body = f"{channel}\0{sender}\0{text}\0{session_id or ''}"; idem = idempotency_key or hashlib.sha256(body.encode()).hexdigest()
        event = GatewayEvent(f"evt_{uuid.uuid4().hex}", str(channel)[:64], str(sender)[:160], str(text)[:20_000], str(session_id or f"gateway_{sender}")[:160], idem[:160], time.time())
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT OR IGNORE INTO gateway_events(idempotency_key,event_id,channel,sender,text,session_id,status,attempts,response,last_error,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                       (event.idempotency_key, event.event_id, event.channel, event.sender, event.text, event.session_id, "pending", 0, None, None, event.created_at, event.created_at))
        return event

    def pending(self, limit: int = 50) -> list[dict[str, Any]]:
        with sqlite3.connect(self.path) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute("SELECT * FROM gateway_events WHERE status IN ('pending','retry') ORDER BY created_at LIMIT ?", (max(1, min(int(limit), 200)),)).fetchall()
        return [dict(row) for row in rows]

    def finish(self, idem: str, response: str) -> None:
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE gateway_events SET status='delivered', response=?, attempts=attempts+1, updated_at=?, last_error=NULL WHERE idempotency_key=?", (str(response)[:40_000], time.time(), idem))

    def fail(self, idem: str, error: str) -> None:
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE gateway_events SET status='retry', attempts=attempts+1, last_error=?, updated_at=? WHERE idempotency_key=?", (str(error)[:2_000], time.time(), idem))


class LocalGateway:
    def __init__(self, ledger: GatewayLedger, handler: Callable[[str, str], str]):
        self.ledger = ledger; self.handler = handler

    def receive(self, *, channel: str, sender: str, text: str, session_id: str | None = None, idempotency_key: str | None = None) -> dict[str, Any]:
        event = self.ledger.ingest(channel=channel, sender=sender, text=text, session_id=session_id, idempotency_key=idempotency_key)
        return self.process(event.idempotency_key)

    def process(self, idempotency_key: str) -> dict[str, Any]:
        rows = [row for row in self.ledger.pending() if row["idempotency_key"] == idempotency_key]
        if not rows:
            return {"status": "already_delivered", "idempotency_key": idempotency_key}
        row = rows[0]
        try:
            response = self.handler(row["text"], row["session_id"])
            self.ledger.finish(idempotency_key, response)
            return {"status": "delivered", "response": response, "idempotency_key": idempotency_key}
        except Exception as exc:
            self.ledger.fail(idempotency_key, str(exc)); return {"status": "retry", "error": str(exc), "idempotency_key": idempotency_key}

    def retry_pending(self, limit: int = 50) -> list[dict[str, Any]]:
        return [self.process(row["idempotency_key"]) for row in self.ledger.pending(limit)]


class WebhookGatewayServer:
    """Authenticated local webhook ingress for Telegram/Discord/custom relays.

    The server is intentionally tiny and loopback-oriented. Channel-specific
    connectors normalize their webhook payloads before calling ``receive``;
    retries are safe because the ledger owns idempotency.
    """
    def __init__(self, gateway: LocalGateway, *, token: str = ""):
        self.gateway, self.token = gateway, token
        self.server: ThreadingHTTPServer | None = None

    def start(self, host: str = "127.0.0.1", port: int = 8787) -> ThreadingHTTPServer:
        gateway, expected = self.gateway, self.token
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                if self.path != "/v1/events": self.send_error(404); return
                if expected and self.headers.get("Authorization", "") != f"Bearer {expected}": self.send_error(401); return
                try:
                    size = min(int(self.headers.get("Content-Length", "0")), 200_000)
                    data = json.loads(self.rfile.read(size))
                    response = gateway.receive(channel=data.get("channel", "webhook"), sender=data.get("sender", "unknown"), text=data.get("text", ""), session_id=data.get("session_id"), idempotency_key=data.get("idempotency_key"))
                    encoded = json.dumps(response).encode("utf-8")
                    self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(encoded))); self.end_headers(); self.wfile.write(encoded)
                except Exception as exc:
                    self.send_error(400, str(exc)[:200])
            def log_message(self, *_args): return
        self.server = ThreadingHTTPServer((host, int(port)), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True, name="smara-gateway").start()
        return self.server

    def stop(self) -> None:
        if self.server is not None:
            self.server.shutdown(); self.server.server_close(); self.server = None


class WebhookChannelAdapter:
    """Outbound adapter for providers that accept a webhook URL."""
    def __init__(self, webhook_url: str):
        if not webhook_url.startswith("https://"):
            raise ValueError("outbound channel webhooks must use HTTPS")
        self.webhook_url = webhook_url

    def send(self, text: str) -> None:
        body = json.dumps({"content": str(text)[:20_000]}).encode("utf-8")
        request = Request(self.webhook_url, data=body, headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(request, timeout=15) as response:
            if response.status >= 300: raise RuntimeError(f"channel webhook returned HTTP {response.status}")
