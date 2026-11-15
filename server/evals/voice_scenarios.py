"""Voice-note test: spoken owner requests through the REAL path (ffmpeg -> Sarvam speech-to-text -> agent -> ledger).

Test audio is made with Sarvam text-to-speech (synthetic Indian voices, not a person), saved as Ogg/Opus like a WhatsApp voice note, in
variants: clean, a second voice, shop-floor noise, phone quality (8 kHz, 8 kbit/s) and fast speech. Every check reads the ledger or the
reply, never what the model says it did. Needs SARVAM_API_KEY and an _eval database.
"""
import asyncio
import base64
import os
import selectors
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

from evals import agent_harness as h
from evals.agent_harness import OWNER, T, Task, has_num, q
from src import clock, tenants
from src.box.sandbox import Sandbox
from src.config import Settings as _Settings
from src.db import migrate
from src.ingest import owner_flow
from src.ingest.dispatch import dispatch
from src.ingest.importer import import_rows
from src.ingest.payload import InboundMessage
from src.ingest.sheet_mapper import SheetRow

KEY = os.environ["SARVAM_API_KEY"]
TTS = "https://api.sarvam.ai/text-to-speech"
CUSTOMERS = [("Agarwal Agencies", "9800000001", "B-101", 220000, 63), ("Singh Electronics", "9800000002", "B-109", 98750, 50),
             ("Chopra Sons", "", "B-118", 42000, 42), ("Gupta Traders", "9800000004", "B-125", 12000, 27),
             ("Gupta Traders", "9800000004", "B-131", 7800, 19), ("Jain Hardware", "9800000005", "B-137", 31000, 14),
             ("Ravi Kirana Store", "9800000006", "B-142", 16575, 6), ("Dutta Agencies", "", "B-148", 6500, -9),
             ("Pandey Sweets", "9800000007", "B-153", 15300, -14), ("Mehta Kirana", "9800000008", "B-160", 3300, -30)]
_DIR = tempfile.mkdtemp(prefix="voice-owner-files-")
h.Settings = lambda: _Settings(owner_files_dir=_DIR, sarvam_api_key=KEY,
                               sarvam_stt_url=os.environ.get("SARVAM_STT_URL") or "https://api.sarvam.ai/speech-to-text")
_orig_read_output = Sandbox.read_output
Sandbox.read_output = lambda self, mid, name: (lambda hits: hits[0].read_bytes() if hits else _orig_read_output(self, mid, name))(
    list(Path(_DIR).glob(f"*/{mid}")))


async def _fetch_media(app, msg):
    return h.FILES[msg.media_id][1], msg.mime or "audio/ogg"


owner_flow.fetch_media = _fetch_media


def rs(r):
    return int(round(r * 100))


async def seed(pool):
    async with pool.connection() as c:
        await c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
    await migrate(pool)
    t = await tenants.create(pool, name="Mehta Distributors", owner_phone=OWNER, upi_vpa="mehta@upi")
    await import_rows(pool, t.id, [SheetRow(n, ("91" + p) if p else "", r, rs(a), T - h.timedelta(days=o)) for n, p, r, a, o in CUSTOMERS])
    async with pool.connection() as c:
        await c.execute("update obligations set next_followup_at = now() + interval '30 days'")
        await c.execute("update parties set last_inbound_at = now()")
        await c.execute("update tenants set owner_last_inbound_at = now()")
    return t


# ---- audio ----------------------------------------------------------------------------------------------------------------
def tts(text: str, lang: str, speaker: str | None = None) -> bytes:
    body = {"text": text, "target_language_code": lang, "model": "bulbul:v3", **({"speaker": speaker} if speaker else {})}
    r = httpx.post(TTS, headers={"api-subscription-key": KEY, "Content-Type": "application/json"}, json=body, timeout=90)
    r.raise_for_status()
    return base64.b64decode(r.json()["audios"][0])


def ff(data: bytes, *args: str, extra_inputs: tuple = ()) -> bytes:
    cmd = ["ffmpeg", "-loglevel", "error", "-i", "pipe:0", *extra_inputs, *args, "pipe:1"]
    return subprocess.run(cmd, input=data, capture_output=True, check=True).stdout


OPUS = ("-ac", "1", "-c:a", "libopus", "-b:a", "24k", "-f", "ogg")
VARIANTS = {
    "clean": lambda wav: ff(wav, *OPUS),
    "noisy": lambda wav: ff(wav, "-filter_complex", "[0:a]aresample=16000[a];[a][1:a]amix=inputs=2:duration=first:weights=1 0.7", *OPUS,
                            extra_inputs=("-f", "lavfi", "-i", "anoisesrc=color=pink:amplitude=0.08:sample_rate=16000")),
    "phone": lambda wav: ff(wav, "-af", "aresample=8000,highpass=f=300,lowpass=f=3400", "-ac", "1", "-c:a", "libopus", "-b:a", "8k", "-f", "ogg"),
    "fast": lambda wav: ff(wav, "-filter:a", "atempo=1.3", *OPUS),
}

# ---- what the owner says, and what must be true afterwards --------------------------------------------------------------
async def texts(env):
    return "\n".join(m.get("text", {}).get("body", "") or m.get("interactive", {}).get("body", {}).get("text", "") for m in env.wa.to(OWNER))


async def heard(env):
    r = await q(env, "select content from chat_messages where kind = 'voice' order by id limit 1")
    return r[0]["content"] if r else "(nothing was transcribed)"


async def ok_call(env):
    t = (await texts(env)).lower()
    a, s = t.find("agarwal"), t.find("singh")
    return a >= 0 and s > a, f"order agarwal={a} singh={s}"


async def ok_phone(env):
    """Heard number: nothing saved before the owner confirms, the digits are read back, and the next voice note saves it."""
    r = await q(env, "select whatsapp_phone from parties where canonical_name = 'Chopra Sons'")
    first = r[0]["whatsapp_phone"]
    said = (await texts(env)).replace(" ", "").replace("-", "")
    heard_back = "9811144444" in said
    if first is not None:
        return False, f"saved before confirming: {first}; read back={heard_back}"
    if not heard_back:
        return False, f"nothing saved but number not read back: {said[-120:]!r}"
    cards = [m for m in env.wa.to(OWNER) if m.get("type") == "interactive"
             and any(b["reply"]["id"].startswith("phone_ok:") for b in m["interactive"]["action"]["buttons"])]
    if cards:                                  # Smara's own read-back card: the owner taps 'Haan, save karo'
        ok_id = [b["reply"]["id"] for b in cards[-1]["interactive"]["action"]["buttons"] if b["reply"]["id"].startswith("phone_ok:")][0]
        await dispatch(env.app, InboundMessage(wamid="tap-" + env.confirm_key, sender=OWNER, kind="interactive", button_id=ok_id))
    else:
        await dispatch(env.app, InboundMessage(wamid="confirm-" + env.confirm_key, sender=OWNER, kind="audio", media_id=env.confirm_key,
                                               mime="audio/ogg", filename=env.confirm_key + ".ogg"))
    r = await q(env, "select whatsapp_phone from parties where canonical_name = 'Chopra Sons'")
    return r[0]["whatsapp_phone"] == "919811144444", f"after confirming saved={r[0]['whatsapp_phone']}"


async def ok_agrawal(env):
    return has_num(await texts(env), "220000"), (await texts(env))[-120:]


async def ok_sale(env):
    r = await q(env, "select o.amount_paise, o.due_date from obligations o join parties p on p.id = o.party_id "
                     "where p.canonical_name = 'Ravi Kirana Store' and o.invoice_ref like 'S-%%'")
    return len(r) == 1 and r[0]["amount_paise"] == 1_200_000 and r[0]["due_date"] == clock.today() + h.timedelta(days=15), f"new={[tuple(x.values()) for x in r]}"


async def ok_cash(env):
    r = {x["invoice_ref"]: x["amount_paise"] for x in await q(env, "select invoice_ref, amount_paise from obligations where invoice_ref in ('B-125','B-131')")}
    return r.get("B-125") == 700_000 and r.get("B-131") == 780_000, f"B-125={r.get('B-125')} B-131={r.get('B-131')}"


async def ok_part(env):
    r = await q(env, "select amount_paise from obligations where invoice_ref = 'B-101'")
    return r[0]["amount_paise"] == 17_000_000, f"B-101={r[0]['amount_paise']}"


async def ok_gupta(env):
    return has_num(await texts(env), "19,800"), (await texts(env))[-120:]


async def ok_summary(env):
    t = await texts(env)
    import re as _re
    found = sorted(set(_re.findall(r"[0-9][0-9,]{4,}", t)))
    return has_num(t, "453,225", "428,125"), f"numbers={found[:20]} | FULL: {t[:900]!r}"


async def ok_reminder(env):
    s = await q(env, "select pending from agent_sessions where status = 'WAITING_APPROVAL'")
    send = [x["pending"]["args"].get("text", "") for x in s if x["pending"].get("name") == "send_whatsapp_message"]
    return bool(send) and "agarwal" in " ".join(send).lower() and "220000" in " ".join(send).replace(",", ""), f"card={[x[:70] for x in send]}"


async def ok_pdf(env):
    pdfs = [u[0] for u in env.wa.uploads if u[0].lower().endswith(".pdf")]
    return any("singh" in p.lower() for p in pdfs), f"pdfs={pdfs}"


UTTERANCES = [  # id, spoken text, language, check, owner taps Approve
    ("call_list", "Aaj kisko call karun? Sabse zyada overdue wale top teen batao.", "hi-IN", ok_call, False),
    ("give_number", "Chopra Sons ka number nau aath ek ek ek chaar chaar chaar chaar chaar hai.", "hi-IN", ok_phone, False),
    ("credit_sale", "Ravi Kirana Store ko aaj barah hazaar rupaye ka maal udhaar diya, pandrah din mein dega.", "hi-IN", ok_sale, True),
    ("cash_in", "Gupta Traders ne paanch hazaar rupaye cash diye.", "hi-IN", ok_cash, True),
    ("part_pay", "Agarwal Agencies ne pachaas hazaar rupaye diye.", "hi-IN", ok_part, True),
    ("balance_variant_spelling", "Agrawal Agency ka kitna baaki hai?", "hi-IN", ok_agrawal, False),
    ("balance", "Gupta Traders ka kitna baaki hai?", "hi-IN", ok_gupta, False),
    ("balance_hindi", "गुप्ता ट्रेडर्स का कितना बाकी है?", "hi-IN", ok_gupta, False),
    ("summary", "Business ka summary do. Kul kitna baaki hai aur kitna overdue hai?", "hi-IN", ok_summary, False),
    ("reminder", "Agarwal Agencies ko payment ka reminder bhejo.", "hi-IN", ok_reminder, False),
    ("statement_english", "Please send me a statement PDF for Singh Electronics.", "en-IN", ok_pdf, False),
]


def build_tasks():
    speakers = [None]
    for name in ("aditya", "shubh", "rahul", "amit", "dev", "priya", "neha", "ritu"):     # a second voice, if the account has one
        try:
            tts("ek", "hi-IN", name)
            speakers.append(name)
            break
        except httpx.HTTPError:
            continue
    print("voices:", speakers, flush=True)
    tasks = []
    for uid, text, lang, check, approve in UTTERANCES:
        wav = tts(text, lang, speakers[0])
        audio = {f"{v}": fn(wav) for v, fn in VARIANTS.items()}
        if len(speakers) > 1:
            audio["voice2"] = VARIANTS["clean"](tts(text, lang, speakers[1]))
        for variant, data in audio.items():
            key = f"VN-{uid}-{variant}"
            h.FILES[key] = (f"{key}.ogg", data)

            if uid == "give_number":
                ckey = f"VN-confirm-{variant}"
                h.FILES[ckey] = (f"{ckey}.ogg", VARIANTS.get(variant, VARIANTS["clean"])(tts("haan, sahi hai", "hi-IN", speakers[0])))

            async def run_check(env, check=check, ckey=f"VN-confirm-{variant}"):
                env.confirm_key = ckey
                ok, detail = await check(env)
                return ok, f"HEARD: {await heard(env)!r} | {detail}"
            msg = InboundMessage(wamid=key, sender=OWNER, kind="audio", media_id=key, mime="audio/ogg", filename=f"{key}.ogg")
            tasks.append(Task(f"{uid}.{variant}", ("owner", msg), run_check, approve=approve))
    return tasks


if __name__ == "__main__":
    h.timedelta = __import__("datetime").timedelta
    h.TASKS[:] = build_tasks()
    h.seed = seed
    asyncio.run(h.main(), loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))
