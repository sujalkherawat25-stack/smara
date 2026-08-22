from __future__ import annotations

import json

from smara.cli import _stream_chat


def test_stream_chat_reads_compact_typed_sse(capsys):
    frames = [
        f"data: {json.dumps({'type': 'phase', 'phase': 'answer'})}\n\n",
        f"data: {json.dumps({'type': 'token', 'text': 'Hello from Smara.'})}\n\n",
        f"data: {json.dumps({'type': 'done', 'memories_used': 0, 'tools_used': 0, 'total_ms': 1})}\n\n",
    ]

    class FakeResponse:
        status_code = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def iter_lines(self):
            return iter(frame.rstrip("\n") for frame in frames for _ in [0])

    class FakeClient:
        def stream(self, method, path, **kwargs):
            assert method == "POST"
            assert path == "/v1/chat/stream"
            return FakeResponse()

    assert _stream_chat(FakeClient(), message="hi", workspace="default", conversation_id="cli_test") == "Hello from Smara."
    captured = capsys.readouterr()
    assert "Hello from Smara." in captured.out
    assert "answer" in captured.out
    assert "1 ms" in captured.out
