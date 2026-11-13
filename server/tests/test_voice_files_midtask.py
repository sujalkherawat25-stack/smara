"""Voice notes, files of any kind (up to 20 MB), and owner messages during a run.

Unit tests need no database. The rest run against Postgres (TEST_DATABASE_URL) with scripted models and fake channels:
a voice note is off without a Sarvam key and is read as typed text with one; a 25 MB file is refused with the limit;
a 3 MB file is kept for the agent and read by its tools; an unknown file is kept and described; a message during a run
is read at its next step and starts no second run; a row left RUNNING by a restart does not swallow messages; and a
message during a run still cannot make the agent message a customer without the owner's approval.
"""
import dataclasses
import json
import logging
import os
import re
import time
from datetime import date, datetime, timezone
from itertools import count
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from src import tenants
from src.config import Settings
from src.db import migrate, open_pool
from src.ingest import owner_files, owner_flow
from src.ingest.dispatch import dispatch
from src.ingest.importer import import_rows
from src.ingest.media import MediaError, download_media, transcribe
from src.ingest.payload import InboundMessage
from src.ingest.sheet_mapper import SheetRow
from src.loop.wa_link import SidecarClient, SidecarError

URL = os.environ.get("TEST_DATABASE_URL")
OWNER, DEBTOR = "919000000001", "919876543210"
SARVAM_KEY = "test-sarvam-key-not-real"
SIDECAR_TOKEN = "t" * 40
SHOP = "0b65d7d2-acc1-4842-8c1a-6460b692982a"
_ids = count(1)


def call(name, **args):
    return {"content": "", "tool_calls": [{"id": f"call-{next(_ids)}", "type": "function",
                                           "function": {"name": name, "arguments": json.dumps(args)}}]}


def say(text):
    return {"content": text}


def ref_in(messages) -> str:
    """The file ref the owner's latest attachment message carries."""
    for m in reversed(messages):
        if m["role"] == "user" and "file_ref=" in str(m["content"]):
            return re.search(r"file_ref=(local:[^;\]\s]+)", m["content"]).group(1)
    raise AssertionError("no attachment in the conversation")


class ScriptLLM:
    """Scripted model. A step is a dict, or a callable (sync or async) that gets the messages and returns one."""
    enabled = True

    def __init__(self, *steps):
        self.steps, self.seen = list(steps), []

    async def chat(self, messages, tools, allow_fallback=True):
        self.seen.append(json.loads(json.dumps(messages)))
        step = self.steps.pop(0) if self.steps else say("done")
        if isinstance(step, Exception):
            raise step
        out = step(messages) if callable(step) else step
        if hasattr(out, "__await__"):
            out = await out
        return out

    def last_tool_result(self, i=-1):
        return json.loads([m for m in self.seen[i] if m["role"] == "tool"][-1]["content"])


class FakeWA:
    def __init__(self):
        self.sent = []

    async def send(self, payload, **kw):
        self.sent.append(payload)
        return f"wamid.{next(_ids)}"

    async def upload_media(self, data, mime, filename, **kw):
        return "media.1"

    def to(self, phone):
        return [m for m in self.sent if m["to"] == phone]

    def texts(self):
        return [m["text"]["body"] for m in self.sent if "text" in m]


class FakeContinuum:
    async def resolve(self, **kw):
        return {"resolution": {"state_card": ""}}


class FakeBrowsers:
    def page(self, sid):
        return {}

    def alive(self, sid):
        return False


async def one(pool, sql, *a):
    async with pool.connection() as c:
        return await (await c.execute(sql, a)).fetchone()


async def column(pool, sql, *a):
    async with pool.connection() as c:
        return [r for r in await (await c.execute(sql, a)).fetchall()]


def owner(wamid, text="", **kw):
    return InboundMessage(wamid=wamid, sender=OWNER, kind=kw.pop("kind", "text"), text=text, **kw)


def media_graph(size: int, mime: str = "text/csv"):
    """A Graph API stand-in: the media's metadata with its declared size, then the bytes."""
    def handler(request: httpx.Request):
        if request.url.path.endswith("/media-meta"):
            return httpx.Response(200, json={"url": "https://cdn.test/file", "mime_type": mime, "file_size": size})
        return httpx.Response(200, content=b"x" * size)
    return handler


@pytest.fixture
async def env(tmp_path):
    if not URL:
        pytest.skip("TEST_DATABASE_URL not set")
    pool = await open_pool(URL)
    async with pool.connection() as c:
        await c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
    await migrate(pool)
    t = await tenants.create(pool, name="Shop", owner_phone=OWNER, upi_vpa="shop@upi")
    await import_rows(pool, t.id, [SheetRow("Ravi", DEBTOR, "INV-1", 1_000_000, date(2026, 11, 1))])
    async with pool.connection() as c:
        pid = (await (await c.execute("SELECT id FROM parties WHERE tenant_id = %s", (t.id,))).fetchone())["id"]
    files = tmp_path / "owner-files"
    settings = Settings(reminder_send_mode="smara", sarvam_api_key=SARVAM_KEY, owner_files_dir=str(files))
    app = SimpleNamespace(state=SimpleNamespace(pool=pool, settings=settings, wa=FakeWA(), tg=None, bg=set(),
                                                continuum=FakeContinuum(), browsers=FakeBrowsers(), llm=ScriptLLM(),
                                                agent_background=False, wa_link=None))
    yield SimpleNamespace(app=app, pool=pool, t=t, pid=str(pid), files=files)
    await pool.close()


# ---------------------------------------------------------------- unit: the file store and retention (no database)

def test_owner_files_are_kept_per_shop_under_a_random_name(tmp_path):
    other = "1c65d7d2-acc1-4842-8c1a-6460b692982a"
    ref = owner_files.save(str(tmp_path), SHOP, "Sales Report 2026.xlsx", b"abc")
    assert ref.startswith("local:") and ref.endswith(":Sales_Report_2026.xlsx")
    token = ref.split(":")[1]
    assert owner_files.read(str(tmp_path), SHOP, token) == b"abc"
    with pytest.raises(owner_files.StoredFileGone):
        owner_files.read(str(tmp_path), other, token)               # another shop cannot open it
    with pytest.raises(owner_files.StoredFileGone):
        owner_files.read(str(tmp_path), SHOP, "../../etc/passwd")    # a name is never a path
    assert not [p for p in tmp_path.rglob("*") if p.is_file() and p.name != token], "the file name is never on disk"


def test_files_are_kept_permanently_and_a_save_never_overwrites(tmp_path):
    assert not hasattr(owner_files, "purge") and not hasattr(owner_files, "purge_due"), "no timer deletes owner files"
    old = owner_files.save(str(tmp_path), SHOP, "sales.csv", b"1")
    eight_days = time.time() - 8 * 86400
    os.utime(tmp_path / SHOP / old.split(":")[1], (eight_days, eight_days))
    again = owner_files.save(str(tmp_path), SHOP, "sales.csv", b"2")
    assert again != old, "the same name saved again gets a new token, so nothing is overwritten"
    assert owner_files.read(str(tmp_path), SHOP, old.split(":")[1]) == b"1", "an eight-day-old file is still kept"
    assert owner_files.read(str(tmp_path), SHOP, again.split(":")[1]) == b"2"


def test_a_shop_space_is_250_mb_and_a_save_past_it_is_refused_with_a_plain_message(tmp_path, monkeypatch):
    assert owner_files.QUOTA_BYTES == 250 * 1024 * 1024
    assert "250 MB" in owner_files.full_text()
    monkeypatch.setattr(owner_files, "QUOTA_BYTES", 10)
    owner_files.save(str(tmp_path), SHOP, "a.csv", b"123456")
    with pytest.raises(owner_files.StorageFull):
        owner_files.save(str(tmp_path), SHOP, "b.csv", b"123456")          # 12 bytes would be over 10
    owner_files.save(str(tmp_path), "1c65d7d2-acc1-4842-8c1a-6460b692982a", "c.csv", b"123456")  # another shop has its own space
    assert owner_files.used_bytes(str(tmp_path), SHOP) == 6


def test_a_file_over_the_limit_is_refused_and_the_reply_says_the_limit(tmp_path):
    assert owner_files.MAX_BYTES == 20 * 1024 * 1024
    text = owner_files.too_large_text(25_000_000)
    assert "25.0 MB" in text and "20 MB limit" in text
    with pytest.raises(ValueError):
        owner_files.save(str(tmp_path), SHOP, "big.pdf", b"x" * (owner_files.MAX_BYTES + 1))


# ---------------------------------------------------------------- unit: Sarvam and the sidecar's media route (no database)

async def test_speech_to_text_sends_the_key_in_a_header_and_returns_the_words():
    seen = {}

    def handler(request: httpx.Request):
        seen.update(key=request.headers.get("api-subscription-key"), url=str(request.url))
        return httpx.Response(200, json={"transcript": "  Ravi ka baaki kitna hai  "})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        text = await transcribe(SARVAM_KEY, b"OggS", "audio/ogg", client=c, url="https://stt.test/speech-to-text")
    assert text == "  Ravi ka baaki kitna hai  "
    assert seen == {"key": SARVAM_KEY, "url": "https://stt.test/speech-to-text"}
    assert await transcribe("", b"OggS") == "", "with no key nothing is sent"


async def test_the_app_fetches_a_held_file_from_the_sidecar_with_its_token():
    ref = "0b65d7d2-acc1-4842-8c1a-6460b692982b"
    seen = []

    def handler(request: httpx.Request):
        seen.append((request.method, request.url.path, request.headers.get("authorization")))
        if request.url.path.endswith(f"/media/{ref}"):
            return httpx.Response(200, content=b"PDF-BYTES")
        return httpx.Response(404, json={"error": "no such file"})

    link = SidecarClient("http://wa-link:8090", SIDECAR_TOKEN, transport=httpx.MockTransport(handler))
    assert await link.media(SHOP, ref) == b"PDF-BYTES"
    assert seen == [("GET", f"/sessions/{SHOP}/media/{ref}", f"Bearer {SIDECAR_TOKEN}")]
    with pytest.raises(SidecarError):
        await link.media(SHOP, "0b65d7d2-acc1-4842-8c1a-6460b692982c")          # the sidecar has no such file
    with pytest.raises(SidecarError):
        await link.media(SHOP, "../../secrets")                                  # refused before any request
    assert len(seen) == 2, "the malformed reference made no request"


def test_a_session_stays_live_while_any_run_of_it_is_still_working():
    from src.agent_loop import _live, _mark
    app = SimpleNamespace(state=SimpleNamespace())
    _mark(app, "s1", True)         # a run starts
    _mark(app, "s1", True)         # its continuation starts before the first has finished (the run is still in its finish)
    _mark(app, "s1", False)        # the first run ends
    assert "s1" in _live(app), "the continuation is still working: a message must join it"
    _mark(app, "s1", False)
    assert "s1" not in _live(app)


async def test_a_25_mb_file_on_whatsapp_is_refused_before_it_is_downloaded():
    async def capped(token, media_id, client=None, max_bytes=owner_files.MAX_BYTES):
        async with httpx.AsyncClient(transport=httpx.MockTransport(media_graph(25_000_000, "application/pdf"))) as c:
            return await download_media(token, media_id, client=c, max_bytes=max_bytes)
    with pytest.raises(MediaError, match="limit"):
        await capped("token", "media-meta")


# ---------------------------------------------------------------- database: voice notes

async def test_a_voice_note_gets_the_not_enabled_reply_while_the_key_is_missing(env, monkeypatch):
    env.app.state.settings = dataclasses.replace(env.app.state.settings, sarvam_api_key="")
    downloads = []

    async def must_not_download(*a, **k):
        downloads.append(a)
        return b"", "audio/ogg"
    monkeypatch.setattr(owner_flow, "download_media", must_not_download)
    await dispatch(env.app, owner("v-off", kind="audio", media_id="m-voice"))
    assert env.app.state.wa.texts()[-1] == owner_flow.VOICE_OFF
    assert "not enabled" in owner_flow.VOICE_OFF
    assert downloads == [], "nothing is downloaded while voice notes are off"
    assert (await one(env.pool, "SELECT count(*) AS n FROM agent_sessions"))["n"] == 0


async def test_a_voice_note_with_a_sarvam_transcript_becomes_the_agents_input_as_if_typed(env, monkeypatch, caplog):
    keys_seen = []

    def stt(request: httpx.Request):
        keys_seen.append(request.headers.get("api-subscription-key"))
        return httpx.Response(200, json={"transcript": "Ravi ka kitna baaki hai?", "language_code": "hi-IN"})

    async def fake_transcribe(api_key, audio, mime="audio/ogg", client=None, url=None):
        async with httpx.AsyncClient(transport=httpx.MockTransport(stt)) as c:
            return await transcribe(api_key, audio, mime, client=c, url=url)

    async def fake_download(token, media_id, client=None, max_bytes=owner_files.MAX_BYTES):
        return b"OggS-voice-bytes", "audio/ogg; codecs=opus"
    monkeypatch.setattr(owner_flow, "transcribe", fake_transcribe)
    monkeypatch.setattr(owner_flow, "download_media", fake_download)
    llm = env.app.state.llm = ScriptLLM(say("Ravi owes Rs 10,000."))
    caplog.set_level(logging.DEBUG)
    await dispatch(env.app, owner("v-on", kind="audio", media_id="m-voice"))
    assert keys_seen == [SARVAM_KEY]
    typed = [m for m in llm.seen[0] if m["role"] == "user"][-1]["content"]
    assert typed == "Ravi ka kitna baaki hai?", "the agent reads the words exactly as if they were typed"
    assert env.app.state.wa.texts()[-1] == "Ravi owes Rs 10,000."
    assert SARVAM_KEY not in caplog.text, "the key is never logged"
    assert (await one(env.pool, "SELECT count(*) AS n FROM agent_sessions"))["n"] == 1


# ---------------------------------------------------------------- database: files of any kind

async def test_a_25_mb_file_is_refused_with_the_limit_and_nothing_is_kept(env, monkeypatch):
    async def capped(token, media_id, client=None, max_bytes=owner_files.MAX_BYTES):
        async with httpx.AsyncClient(transport=httpx.MockTransport(media_graph(25_000_000, "application/pdf"))) as c:
            return await download_media(token, media_id, client=c, max_bytes=max_bytes)
    monkeypatch.setattr(owner_flow, "download_media", capped)
    llm = env.app.state.llm = ScriptLLM(say("should not run"))
    await dispatch(env.app, owner("big-1", kind="document", media_id="media-meta", filename="scan.pdf", mime="application/pdf"))
    reply = env.app.state.wa.texts()[-1]
    assert "limit" in reply and "20 MB" in reply, reply
    assert llm.seen == [], "the agent never sees a refused file"
    assert not env.files.exists() or not any(p.is_file() for p in env.files.rglob("*"))
    assert (await one(env.pool, "SELECT count(*) AS n FROM agent_sessions"))["n"] == 0


async def test_a_file_over_the_limit_is_refused_from_the_channels_own_size_before_any_download(env, monkeypatch):
    async def must_not_download(*a, **k):
        raise AssertionError("downloaded a file that was already over the limit")
    monkeypatch.setattr(owner_flow, "download_media", must_not_download)
    await dispatch(env.app, owner("big-2", kind="video", media_id="tg-video", size=25_000_000))
    assert "20 MB limit" in env.app.state.wa.texts()[-1]


async def test_a_3_mb_sheet_is_kept_for_the_agent_and_its_tools_read_it(env, monkeypatch):
    body = b"party,amount\n" + b"Ravi,1000\n" * (3 * 1024 * 1024 // 10)
    assert len(body) > 3_000_000

    async def fake_download(token, media_id, client=None, max_bytes=owner_files.MAX_BYTES):
        return body, "text/csv"
    monkeypatch.setattr(owner_flow, "download_media", fake_download)
    llm = env.app.state.llm = ScriptLLM(lambda m: call("read_attachment", file_ref=ref_in(m)), say("It lists the payments."))
    await dispatch(env.app, owner("sheet-1", kind="document", media_id="m-sheet", filename="payments.csv", mime="text/csv",
                                 caption="what did they pay"))
    ref = ref_in(llm.seen[0])
    # the tool result is cut to 6000 characters for the model (tools.bounded), so check the fields that come first
    raw = [m for m in llm.seen[1] if m["role"] == "tool"][-1]["content"]
    assert '"kind": "text"' in raw and '"filename": "payments.csv"' in raw and f'"size_bytes": {len(body)}' in raw
    kept = list(env.files.rglob("*"))
    kept_files = [p for p in kept if p.is_file()]
    assert len(kept_files) == 1 and kept_files[0].stat().st_size == len(body)
    assert kept_files[0].name == ref.split(":")[1], "the file on disk is named by its token"
    session = await one(env.pool, "SELECT files FROM agent_sessions")
    assert session["files"] == [ref], "the file belongs to this conversation, so the tools may open it"
    assert env.app.state.wa.texts()[-1] == "It lists the payments."


async def test_an_unknown_file_type_is_kept_and_described_never_run(env, monkeypatch):
    async def fake_download(token, media_id, client=None, max_bytes=owner_files.MAX_BYTES):
        return b"MZ\x90\x00\x03binary", "application/x-msdownload"
    monkeypatch.setattr(owner_flow, "download_media", fake_download)
    llm = env.app.state.llm = ScriptLLM(lambda m: call("read_attachment", file_ref=ref_in(m)), say("It is a program file."))
    await dispatch(env.app, owner("exe-1", kind="document", media_id="m-exe", filename="setup.exe", mime="application/x-msdownload"))
    result = llm.last_tool_result(1)
    assert result["kind"] == "other" and "cannot be read directly" in result["note"]
    assert ref_in(llm.seen[0]).startswith("local:") and ref_in(llm.seen[0]).endswith(":setup.exe")


async def test_a_file_on_the_shops_own_sim_is_fetched_from_the_sidecar_and_kept(env):
    ref = "0b65d7d2-acc1-4842-8c1a-6460b692982b"
    calls = []

    def sidecar(request: httpx.Request):
        calls.append((request.method, request.url.path))
        if request.method == "GET" and request.url.path.endswith(f"/media/{ref}"):
            return httpx.Response(200, content=b"sim,amount\nRavi,1000\n")
        return httpx.Response(200, json={"message_id": "SIM-1"})          # the reply goes back out on the SIM
    env.app.state.wa_link = SidecarClient("http://wa-link:8090", SIDECAR_TOKEN, transport=httpx.MockTransport(sidecar))
    llm = env.app.state.llm = ScriptLLM(lambda m: call("read_attachment", file_ref=ref_in(m)), say("Read it."))
    await dispatch(env.app, InboundMessage(wamid="sim-1", sender=OWNER, kind="document", media_id=ref, filename="sheet.csv",
                                           mime="text/csv", size=30, channel="wa_link", link_tenant_id=env.t.id))
    assert ("GET", f"/sessions/{env.t.id}/media/{ref}") in calls
    assert llm.last_tool_result(1)["kind"] == "text"


async def test_a_voice_note_on_the_shops_sim_is_transcribed_from_the_sidecar(env, monkeypatch):
    ref = "0b65d7d2-acc1-4842-8c1a-6460b692982d"

    def sidecar(request: httpx.Request):
        if request.method == "GET":
            return httpx.Response(200, content=b"OggS-sim-voice")
        return httpx.Response(200, json={"message_id": "SIM-2"})

    def stt(request: httpx.Request):
        return httpx.Response(200, json={"transcript": "Ravi ko reminder bhejo"})

    async def fake_transcribe(api_key, audio, mime="audio/ogg", client=None, url=None):
        assert audio == b"OggS-sim-voice"
        async with httpx.AsyncClient(transport=httpx.MockTransport(stt)) as c:
            return await transcribe(api_key, audio, mime, client=c, url=url)
    monkeypatch.setattr(owner_flow, "transcribe", fake_transcribe)
    env.app.state.wa_link = SidecarClient("http://wa-link:8090", SIDECAR_TOKEN, transport=httpx.MockTransport(sidecar))
    llm = env.app.state.llm = ScriptLLM(say("Noted."))
    await dispatch(env.app, InboundMessage(wamid="sim-v", sender=OWNER, kind="audio", media_id=ref, channel="wa_link",
                                           link_tenant_id=env.t.id))
    assert [m for m in llm.seen[0] if m["role"] == "user"][-1]["content"] == "Ravi ko reminder bhejo"


# ---------------------------------------------------------------- database: owner messages during a run

async def test_a_message_during_a_run_is_read_at_its_next_step_and_starts_no_second_run(env):
    app = env.app

    async def step1(messages):
        await dispatch(app, owner("mid-1", "also check Ravi's payment"))          # the owner writes while step 1 works
        return call("query_ledger_sql", sql="select 1 as ok")

    llm = app.state.llm = ScriptLLM(step1, say("Ravi's payment is checked too."))
    await dispatch(app, owner("task-1", "Ravi ka hisaab batao"))
    later = [m for m in llm.seen[1] if m["role"] == "user"]
    assert any("also check Ravi's payment" in m["content"] for m in later), "read at the next step"
    assert [s["n"] async for s in _rows(env.pool, "SELECT count(*) AS n FROM agent_sessions")] == [1], "one run, not two"
    row = await one(env.pool, "SELECT status, inbox FROM agent_sessions")
    assert row == {"status": "DONE", "inbox": []}
    assert app.state.wa.texts()[-1] == "Ravi's payment is checked too."


async def test_a_message_that_arrives_as_the_run_finishes_is_still_read(env):
    app = env.app

    async def step1(messages):
        await dispatch(app, owner("late-1", "one more thing: remind me tomorrow"))
        return say("Here is the answer so far.")           # no tool call: the run would end here

    llm = app.state.llm = ScriptLLM(step1, say("Noted: I will remind you tomorrow."))
    await dispatch(app, owner("task-2", "status?"))
    assert len(llm.seen) == 2
    assert "remind me tomorrow" in json.dumps(llm.seen[1])
    assert app.state.wa.texts()[-2:] == ["Here is the answer so far.", "Noted: I will remind you tomorrow."]
    assert (await one(env.pool, "SELECT status, steps FROM agent_sessions"))["status"] == "DONE"


async def test_a_message_during_a_run_that_then_fails_is_said_not_read_rather_than_silently_dropped(env):
    app = env.app

    async def step1(messages):
        await dispatch(app, owner("f-1", "and also this"))
        raise RuntimeError("the model broke")
    app.state.llm = ScriptLLM(step1)
    await dispatch(app, owner("task-3", "start"))
    assert (await one(env.pool, "SELECT status FROM agent_sessions"))["status"] == "FAILED"
    assert "was not read" in app.state.wa.texts()[-1]


async def test_a_row_left_running_by_a_restart_does_not_swallow_the_next_message(env):
    async with env.pool.connection() as c:
        await c.execute("INSERT INTO agent_sessions (tenant_id, trust, trigger, channel) VALUES (%s, 'TRUSTED', 'owner_message', 'whatsapp')",
                        (env.t.id,))
    env.app.state.llm = ScriptLLM(say("fresh answer"))
    await dispatch(env.app, owner("after-restart", "hello"))
    assert env.app.state.wa.texts()[-1] == "fresh answer", "answered by a new run, not parked on the dead one"
    stale = await one(env.pool, "SELECT inbox FROM agent_sessions WHERE status = 'RUNNING'")
    assert stale["inbox"] == []


async def test_a_message_during_a_run_still_cannot_make_the_agent_message_a_customer_without_approval(env):
    app = env.app

    async def step1(messages):
        await dispatch(app, owner("mid-ap", "tell Ravi to pay now"))
        return say("Working on it.")

    app.state.llm = ScriptLLM(step1, lambda m: call("send_whatsapp_message", to=env.pid, text="Ravi, please pay Rs 10,000 today."))
    await dispatch(app, owner("task-4", "remind Ravi"))
    assert app.state.wa.to(DEBTOR) == [], "no customer message leaves without the owner's approval"
    assert (await one(env.pool, "SELECT status FROM agent_sessions"))["status"] == "WAITING_APPROVAL"
    assert app.state.wa.to(OWNER), "the owner is asked instead"


async def _rows(pool, sql, *a):
    async with pool.connection() as c:
        for r in await (await c.execute(sql, a)).fetchall():
            yield r


@pytest.fixture(autouse=True)
def _stub_wav_conversion(monkeypatch):
    """The real conversion runs ffmpeg, which is in the app image. These tests check the speech call, not ffmpeg."""
    from src.ingest import media as _media

    async def _passthrough(audio: bytes) -> bytes:
        return audio

    monkeypatch.setattr(_media, "to_wav", _passthrough)
