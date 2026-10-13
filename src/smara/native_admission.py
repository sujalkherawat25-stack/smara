"""Cross-process provider admission. Stores hashes and leases, never secrets.

Admission limits concurrency and request bursts, not billing. Uncertain calls
remain charged to the request window. A lost process lease expires; live streams
renew it. No rejected request is sent and no provider operation is auto-retried.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
from pathlib import Path
import sqlite3
import threading
import time
import uuid
import select
import socket

_request_peer = ContextVar("smara_provider_request_peer", default=None)


@contextmanager
def peer_context(connection):
    token = _request_peer.set(connection)
    try: yield
    finally: _request_peer.reset(token)


class Cancellation:
    def __init__(self, stopped, peer=None):
        self.stopped, self.peer = stopped, peer
    def is_set(self):
        if self.stopped.is_set(): return True
        if self.peer is None: return False
        try:
            readable, _, _ = select.select([self.peer], [], [], 0)
            return bool(readable and self.peer.recv(1, socket.MSG_PEEK) == b"")
        except OSError:
            return True
    def set(self): self.stopped.set()
    def wait(self, seconds):
        self.stopped.wait(seconds)
        return self.is_set()


class AdmissionError(RuntimeError):
    pass


class ProviderAdmission:
    def __init__(self, path, identity, *, concurrent=2, per_minute=60, wait_seconds=30,
                 lease_seconds=600, clock=time.time):
        if concurrent < 1 or per_minute < 1 or wait_seconds < 0 or lease_seconds < 1:
            raise ValueError("Invalid provider admission limits")
        self.path, self.identity = Path(path), identity
        self.concurrent, self.per_minute = concurrent, per_minute
        self.wait_seconds, self.lease_seconds, self.clock = wait_seconds, lease_seconds, clock

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=5)
        db.executescript("""
            CREATE TABLE IF NOT EXISTS provider_leases
                (id TEXT PRIMARY KEY, provider TEXT NOT NULL, expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS provider_requests
                (provider TEXT NOT NULL, created REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS provider_requests_idx ON provider_requests(provider,created);
        """)
        try:
            with db:
                yield db
        finally:
            db.close()

    def acquire(self, cancelled=None):
        deadline = time.monotonic() + self.wait_seconds
        identity = uuid.uuid4().hex
        while True:
            if cancelled is not None and cancelled.is_set():
                raise AdmissionError("Provider request cancelled before admission")
            now = self.clock()
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("DELETE FROM provider_leases WHERE expires<=?", (now,))
                db.execute("DELETE FROM provider_requests WHERE created<=?", (now - 60,))
                active = db.execute("SELECT COUNT(*) FROM provider_leases WHERE provider=?", (self.identity,)).fetchone()[0]
                recent = db.execute("SELECT COUNT(*) FROM provider_requests WHERE provider=?", (self.identity,)).fetchone()[0]
                if active < self.concurrent and recent < self.per_minute:
                    db.execute("INSERT INTO provider_leases VALUES(?,?,?)", (identity, self.identity, now + self.lease_seconds))
                    db.execute("INSERT INTO provider_requests VALUES(?,?)", (self.identity, now))
                    return identity
            if time.monotonic() >= deadline:
                raise AdmissionError("Shared provider capacity reached; request was not sent. Retry later, without replaying completed actions.")
            if cancelled is not None:
                cancelled.wait(.1)
            else:
                time.sleep(.1)

    def release(self, identity):
        with self.connect() as db:
            db.execute("DELETE FROM provider_leases WHERE id=? AND provider=?", (identity, self.identity))

    @contextmanager
    def slot(self, cancelled=None, on_lost=None):
        identity = self.acquire(cancelled)
        stop = threading.Event()
        def renew():
            while not stop.wait(min(10, self.lease_seconds / 3)):
                try:
                    with self.connect() as db:
                        db.execute("UPDATE provider_leases SET expires=? WHERE id=? AND provider=?",
                                   (self.clock() + self.lease_seconds, identity, self.identity))
                except (OSError, sqlite3.Error):
                    # Fail closed: a renewal failure cancels the owning adapter.
                    if cancelled is not None:
                        cancelled.set()
                    if on_lost is not None:
                        on_lost()
                    return
        worker = threading.Thread(target=renew, daemon=True, name="smara-provider-lease")
        worker.start()
        try:
            yield
        finally:
            stop.set(); worker.join(timeout=5)
            self.release(identity)


class AdmissionClient:
    def __init__(self, client, admission):
        self.client, self.admission = client, admission
        self.cancelled = threading.Event()

    @contextmanager
    def stream(self, *args, **kwargs):
        cancelled = Cancellation(self.cancelled, _request_peer.get())
        with self.admission.slot(cancelled, self.close):
            if cancelled.is_set():
                raise AdmissionError("Provider adapter stopped before request was sent")
            with self.client.stream(*args, **kwargs) as response:
                yield response

    def close(self):
        self.cancelled.set()
        self.client.close()


def provider_admission(endpoint):
    from .native_profiles import state_path
    # Same account/host shares capacity across models, profiles and processes.
    from urllib.parse import urlsplit
    identity = hashlib.sha256((urlsplit(endpoint.base_url).netloc.lower() + "\0" + endpoint.api_key).encode()).hexdigest()
    return ProviderAdmission(state_path().parent / "native-provider-admission.sqlite", identity)
