import asyncio
import base64

import pytest

from smara.attachments import AttachmentStore


class FakeUpload:
    def __init__(self, name: str, content: bytes, content_type: str):
        self.filename = name
        self.content_type = content_type
        self._content = content

    async def read(self, size: int = -1) -> bytes:
        if not self._content:
            return b""
        chunk, self._content = self._content[:size], self._content[size:]
        return chunk


def test_attachment_store_extracts_text_and_scopes_account(tmp_path):
    store = AttachmentStore(tmp_path)
    record = asyncio.run(store.save("acct_a", FakeUpload("notes.txt", b"Smara attachment smoke test", "text/plain")))
    context, records = store.context_for("acct_a", [record["id"]])
    assert "Smara attachment smoke test" in context
    assert records[0]["filename"] == "notes.txt"
    assert store.get("acct_b", record["id"]) is None


def test_attachment_store_inlines_only_images(tmp_path):
    store = AttachmentStore(tmp_path)
    image = asyncio.run(store.save("acct_a", FakeUpload("pixel.png", b"png-bytes", "image/png")))
    text = asyncio.run(store.save("acct_a", FakeUpload("notes.txt", b"hello", "text/plain")))
    inputs = store.image_inputs("acct_a", [image["id"], text["id"]])
    assert len(inputs) == 1
    assert inputs[0]["filename"] == "pixel.png"
    assert inputs[0]["data_url"].startswith("data:image/png;base64,")
    assert base64.b64decode(inputs[0]["data_url"].split(",", 1)[1]) == b"png-bytes"


def test_attachment_store_enforces_per_file_limit(tmp_path):
    store = AttachmentStore(tmp_path)
    with pytest.raises(ValueError, match="100 MB"):
        asyncio.run(store.save("acct_a", FakeUpload("large.bin", b"1234", "application/octet-stream"), size_limit=3))
