"""Reader boundary tests; fixture HTTP data is not live-model evidence."""
import asyncio
import io
import json
import pytest
import httpx
from smara.native_tools import NativeTools, PAGE_CHARS, MAX_BYTES, page, serve


def call(tools, name, **kwargs):
    return asyncio.run(tools.call(name, kwargs))


def test_clock_and_empty_memory_are_local_nonmutating(tmp_path):
    tools = NativeTools(tmp_path)
    assert "+" in call(tools, "current_time")["utc"]
    assert call(tools, "memory_read")["exists"] is False
    assert list(tmp_path.iterdir()) == []


def test_public_search_only_excludes_local_memory_and_other_readers(tmp_path):
    tools = NativeTools(tmp_path, public_search_only=True)
    exposed = {spec["name"] for spec in tools.tool_specs}
    assert exposed == {"current_time", "web_search", "fetch_url", "source_page"}
    with pytest.raises(ValueError, match="Unknown tool"):
        call(tools, "memory_read")

    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    output = io.StringIO()
    serve(tmp_path, io.StringIO("\n".join(json.dumps(item) for item in requests) + "\n"), output,
          public_search_only=True)
    listed = json.loads(output.getvalue().splitlines()[1])["result"]["tools"]
    assert {spec["name"] for spec in listed} == exposed


def test_memory_pagination_has_no_silent_loss(tmp_path):
    directory = tmp_path / ".smara"
    directory.mkdir()
    text = "memory 😀 " * 2500
    (directory / "native-memory.md").write_text(text, encoding="utf-8")
    tools = NativeTools(tmp_path)
    parts, offset = [], 0
    while offset is not None:
        result = call(tools, "memory_read", offset=offset)
        parts.append(result["text"])
        offset = result["next_offset"]
    assert "".join(parts) == text
    assert result["total_chars"] == len(text)
    assert result["untrusted_content"]


@pytest.mark.parametrize("name,args", [("exec", {}), ("memory_read", {"path": "private.txt"}), ("memory_read", {"offset": -1}), ("web_search", {"query": "x", "max_results": 99}), ("fetch_url", {"url": False})])
def test_no_arbitrary_file_command_or_invalid_arguments(tmp_path, name, args):
    with pytest.raises(ValueError):
        asyncio.run(NativeTools(tmp_path).call(name, args))


def test_memory_symlink_escape_denied(tmp_path):
    outside = tmp_path.parent / "synthetic-memory-outside"
    outside.mkdir(exist_ok=True)
    try:
        (tmp_path / ".smara").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("This Windows account cannot create symbolic links")
    with pytest.raises(ValueError, match="escapes"):
        call(NativeTools(tmp_path), "memory_read")


def test_fetch_preserves_full_text_and_provenance(tmp_path, monkeypatch):
    monkeypatch.setattr("smara.research._is_public_http_url", lambda url: url.startswith("https://public.example/"))
    body = "source evidence " * 1500
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, text=body, headers={"content-type": "text/plain"})))
    tools = NativeTools(tmp_path, client)
    first = call(tools, "fetch_url", url="https://public.example/source")
    rest = call(tools, "source_page", source_id=first["source_id"], offset=PAGE_CHARS)
    last = call(tools, "source_page", source_id=first["source_id"], offset=rest["next_offset"])
    assert first["text"] + rest["text"] + last["text"] == body
    assert len(first["content_sha256"]) == 64
    assert first["retrieved_at"] and first["published_at"] is None
    asyncio.run(client.aclose())


def test_fetch_rejects_redirect_to_private_and_large_page(tmp_path, monkeypatch):
    monkeypatch.setattr("smara.research._is_public_http_url", lambda url: url.startswith("https://public.example/"))
    requests = []
    def redirect(request):
        requests.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://127.0.0.1/private"})
    client = httpx.AsyncClient(transport=httpx.MockTransport(redirect))
    with pytest.raises(ValueError, match="publicly routable"):
        call(NativeTools(tmp_path, client), "fetch_url", url="https://public.example/source")
    assert len(requests) == 1
    asyncio.run(client.aclose())
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"x" * (MAX_BYTES + 1), headers={"content-type": "text/plain"})))
    with pytest.raises(ValueError, match="not silently truncated"):
        call(NativeTools(tmp_path, client), "fetch_url", url="https://public.example/source")
    asyncio.run(client.aclose())


def test_mcp_stdio_and_error_are_not_success(tmp_path):
    requests = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "exec"}}]
    output = io.StringIO()
    assert serve(tmp_path, io.StringIO("\n".join(json.dumps(r) for r in requests) + "\n"), output) == 0
    results = [json.loads(line) for line in output.getvalue().splitlines()]
    assert [r["id"] for r in results] == [1, 2, 3]
    specs = results[1]["result"]["tools"]
    assert all(t["annotations"]["readOnlyHint"] for t in specs if t["name"] not in {"document_ocr", "image_analyze"})
    assert all(not t["annotations"]["readOnlyHint"] for t in specs if t["name"] in {"document_ocr", "image_analyze"})
    assert results[2]["result"]["isError"] is True


def test_mcp_frames_preserve_unicode_on_windows_codepages(tmp_path):
    directory = tmp_path / ".smara"
    directory.mkdir()
    expected = "स्मारा 😀 → 日本語"
    (directory / "native-memory.md").write_text(expected, encoding="utf-8")
    requests = [{"jsonrpc": "2.0", "id": 1, "method": "initialize"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "memory_read"}}]
    raw = io.BytesIO()
    legacy_writer = io.TextIOWrapper(raw, encoding="cp1252")
    serve(tmp_path, io.StringIO("\n".join(json.dumps(r) for r in requests) + "\n"), legacy_writer)
    reply = json.loads(raw.getvalue().decode("utf-8").splitlines()[-1])
    assert json.loads(reply["result"]["content"][0]["text"])["text"] == expected


def test_unsupported_pdf_failure_is_actionable_cached_and_secret_free(tmp_path, monkeypatch):
    monkeypatch.setattr("smara.research._is_public_http_url", lambda url: True)
    requests = []
    def reply(request):
        requests.append(request)
        return httpx.Response(200, content=b"%PDF-fixture", headers={"content-type": "application/pdf"})
    client = httpx.AsyncClient(transport=httpx.MockTransport(reply))
    tools = NativeTools(tmp_path, client)
    for _ in range(10):
        with pytest.raises(ValueError, match="Unsupported source type"):
            call(tools, "fetch_url", url="https://example.com/rules.pdf")
    assert len(requests) == 1
    asyncio.run(client.aclose())


def test_successful_fetch_is_cached_without_losing_source_pagination(tmp_path, monkeypatch):
    monkeypatch.setattr("smara.research._is_public_http_url", lambda url: True)
    requests = []
    def reply(request):
        requests.append(request)
        return httpx.Response(200, text="evidence " * 1500, headers={"content-type": "text/plain"})
    client = httpx.AsyncClient(transport=httpx.MockTransport(reply))
    tools = NativeTools(tmp_path, client)
    first = call(tools, "fetch_url", url="https://example.com/x")
    second = call(tools, "fetch_url", url="https://example.com/x")
    assert second["cached"] and second["retrieved_at"] == first["retrieved_at"]
    assert len(requests) == 1
    assert call(tools, "source_page", source_id=first["source_id"], offset=PAGE_CHARS)["text"]
    tools.sources.clear()
    assert "cached" not in call(tools, "fetch_url", url="https://example.com/x")
    assert len(requests) == 2
    asyncio.run(client.aclose())


def test_document_cache_tracks_file_content_not_only_path(tmp_path, monkeypatch):
    import hashlib

    image = tmp_path / "same-name.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nfirst synthetic image")
    calls = []

    async def describe(workspace, path, question, **_kwargs):
        content = (workspace / path).read_bytes()
        calls.append(content)
        return {"text": f"analysis {len(calls)}", "content_sha256": hashlib.sha256(content).hexdigest()}

    monkeypatch.setattr("smara.native_documents.describe_image", describe)
    tools = NativeTools(tmp_path)
    first = call(tools, "image_analyze", path=image.name, question="What is shown?")
    cached = call(tools, "image_analyze", path=image.name, question="What is shown?")
    assert cached["cached"] is True
    assert len(calls) == 1

    image.write_bytes(b"\x89PNG\r\n\x1a\nreplacement synthetic image")
    replaced = call(tools, "image_analyze", path=image.name, question="What is shown?")
    assert "cached" not in replaced
    assert replaced["text"] != first["text"]
    assert len(calls) == 2


def test_http_error_does_not_leak_response_or_credential_url(tmp_path, monkeypatch):
    from smara.native_tools import reader_error
    monkeypatch.setattr("smara.research._is_public_http_url", lambda url: True)
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(403, text="synthetic-secret")))
    tools = NativeTools(tmp_path, client)
    with pytest.raises(httpx.HTTPStatusError) as caught:
        call(tools, "fetch_url", url="https://example.com/x?token=synthetic-secret")
    assert "HTTP 403" in reader_error(caught.value)
    assert "synthetic-secret" not in reader_error(caught.value)
    asyncio.run(client.aclose())
