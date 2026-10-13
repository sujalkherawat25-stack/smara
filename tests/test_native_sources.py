import asyncio
import json
import sqlite3
import httpx
import pytest
from smara.native_sources import SourceCache, MAX_PROJECT_RECORDS, TTL
from smara.native_tools import NativeTools, PAGE_CHARS, ReaderError


def test_source_page_survives_cold_reader_restart_without_network(tmp_path, monkeypatch):
    monkeypatch.setattr("smara.research._is_public_http_url", lambda url: url.startswith("https://public.example/"))
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(200, text="Actual synthetic evidence. " * 1500, headers={"content-type": "text/plain"})
    client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    cache = SourceCache(tmp_path / "state" / "sources.sqlite", tmp_path)
    first = asyncio.run(NativeTools(tmp_path, client, source_cache=cache).call("fetch_url", {"url": "https://public.example/source"}))
    restarted = NativeTools(tmp_path, client, source_cache=cache)
    next_page = asyncio.run(restarted.call("source_page", {"source_id": first["source_id"], "offset": PAGE_CHARS}))
    assert next_page["restored_snapshot"] is True
    assert next_page["retrieved_at"] == first["retrieved_at"]
    assert "not a live refresh" in next_page["freshness_note"]
    assert next_page["text"] and len(requests) == 1
    asyncio.run(client.aclose())


def test_cache_project_isolation_expiration_and_bound(tmp_path, monkeypatch):
    monkeypatch.setattr("smara.research._is_public_http_url", lambda url: url == "https://public.example/source")
    now = [1000]
    path = tmp_path / "sources.sqlite"
    first = SourceCache(path, tmp_path / "one", clock=lambda: now[0])
    second = SourceCache(path, tmp_path / "two", clock=lambda: now[0])
    for n in range(MAX_PROJECT_RECORDS + 2):
        first.save({"source_id": str(n), "text": "untrusted", "final_url": "https://public.example/source", "retrieved_at": "synthetic timestamp"})
        now[0] += 1
    assert first.load("0") is None
    assert first.load(str(n))["text"] == "untrusted"
    assert second.load(str(n)) is None
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM public_sources").fetchone()[0] == MAX_PROJECT_RECORDS
    now[0] += TTL + 1
    assert first.load(str(n)) is None


def test_broken_source_snapshot_is_not_fabricated_or_returned_as_success(tmp_path):
    cache = SourceCache(tmp_path / "sources.sqlite", tmp_path)
    with cache.connect() as db:
        db.execute("INSERT INTO public_sources VALUES(?,?,?,?)", (cache.scope, "a" * 64, "not json", cache.clock()))
    with pytest.raises(ReaderError, match="No page was fabricated"):
        asyncio.run(NativeTools(tmp_path, source_cache=cache).call("source_page", {"source_id": "a" * 64}))
