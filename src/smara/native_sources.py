"""Bounded, project-scoped public-page snapshots for cold session recovery.

Not conversation memory. No document/OCR bytes, cookies, secrets, or live
refreshes. A restored page retains its original retrieval time and is untrusted.
"""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import time

MAX_RECORD_BYTES = 2_000_000
MAX_PROJECT_RECORDS = 32
MAX_RECORDS = 256
TTL = 86400


class SourceCache:
    def __init__(self, path, workspace, clock=time.time):
        from .native_goals import workspace_key
        self.path, self.clock = Path(path), clock
        self.scope = hashlib.sha256(workspace_key(workspace).encode()).hexdigest()

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=5)
        db.execute("CREATE TABLE IF NOT EXISTS public_sources (scope TEXT, id TEXT, record TEXT NOT NULL, created REAL NOT NULL, PRIMARY KEY(scope,id))")
        try:
            with db: yield db
        finally: db.close()

    def save(self, record):
        encoded = json.dumps(record, ensure_ascii=False)
        if len(encoded.encode()) > MAX_RECORD_BYTES:
            raise ValueError("Public source snapshot is too large")
        with self.connect() as db:
            db.execute("DELETE FROM public_sources WHERE created<?", (self.clock() - TTL,))
            db.execute("INSERT OR REPLACE INTO public_sources VALUES(?,?,?,?)", (self.scope, record["source_id"], encoded, self.clock()))
            db.execute("DELETE FROM public_sources WHERE scope=? AND id NOT IN (SELECT id FROM public_sources WHERE scope=? ORDER BY created DESC, id LIMIT ?)", (self.scope, self.scope, MAX_PROJECT_RECORDS))
            db.execute("DELETE FROM public_sources WHERE rowid NOT IN (SELECT rowid FROM public_sources ORDER BY created DESC, rowid DESC LIMIT ?)", (MAX_RECORDS,))

    def load(self, identifier):
        if not self.path.is_file(): return None
        with self.connect() as db:
            row = db.execute("SELECT record FROM public_sources WHERE scope=? AND id=? AND created>=?", (self.scope, identifier, self.clock() - TTL)).fetchone()
        if row is None or len(row[0].encode()) > MAX_RECORD_BYTES: return None
        record = json.loads(row[0])
        if (not isinstance(record, dict) or record.get("source_id") != identifier
                or not isinstance(record.get("text"), str) or not isinstance(record.get("retrieved_at"), str)
                or not isinstance(record.get("final_url"), str)):
            raise ValueError("Invalid public source snapshot")
        from .research import _is_public_http_url
        if not _is_public_http_url(record["final_url"]):
            raise ValueError("Source snapshot is not a public page")
        return record
