"""Rehearse the whole demo as ONE conversation with a fresh shop: the real model, the real agent loop, scratch Postgres, fake WhatsApp.

    python -m evals.demo_rehearsal --db postgresql://.../smara_eval --model glm5.3-flash --sandbox container --jobs /jobs

The steps are the ones in docs/demo/DEMO_SCRIPT.md, in that order, with the same messages. After each step the owner's approval taps
are pressed for them, and the replies and the time are printed, so the output reads like the demo. At the end the ledger is checked
(new invoice, cash applied oldest-first, numbers saved, PDFs sent). The harness DROPs the public schema: the database name must end
in _eval. Use --sandbox container --jobs /jobs on the server so documents are made by the real sandbox.
"""
import asyncio
import selectors
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

from evals import agent_harness as h
from evals.agent_harness import OWNER, Task, owner, q
from src import tenants
from src.box.sandbox import Sandbox
from src.config import Settings as _Settings
from src.db import migrate
from src.ingest import owner_flow
from src.ingest.dispatch import dispatch
from src.ingest.payload import InboundMessage

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "docs" / "demo"))
import make_demo_files as demo  # noqa: E402

h.FILES.update({"DM1": ("1_Outstanding_Receivables.xlsx", demo.receivables_xlsx()),
                "DM2": ("2_Purchase_Register_Oct.xlsx", demo.purchase_register_xlsx()),
                "DM3": ("3_GSTR2B_Oct.xlsx", demo.gstr2b_xlsx())})
_DIR = tempfile.mkdtemp(prefix="demo-rehearsal-files-")          # kept files go here, never to the real owner-files volume
h.Settings = lambda: _Settings(owner_files_dir=_DIR)

STEPS = [  # label, message, file key or None
    ("1  Send the Tally export", "Ye mera outstanding hai.", "DM1"),
    ("2  Who to call today", "Aaj kisko call karun? Sabse zyada overdue wale top 3 batao, amount ke saath.", None),
    ("3  Give the missing numbers", "Chopra Sons 9811144444, Dutta Agencies 98222 55555", None),
    ("4  Ask for a reminder", "Chopra Sons ko payment ka reminder bhejo, UPI ke saath.", None),
    ("5  Record a sale on credit", "Ravi Kirana Store ko aaj 12000 ka maal udhaar diya, 15 din mein dega.", None),
    ("6  Record cash received", "Gupta Traders ne 5000 cash diya.", None),
    ("7  Check the books changed", "Gupta Traders ka ab kitna baaki hai?", None),
    ("8  Send the purchase register", "Ye mera purchase register hai.", "DM2"),
    ("9  Send GSTR-2B, ask to match", "Ye GSTR-2B hai. Dono match karo: kitna ITC miss hai aur kis supplier ko chase karun?", "DM3"),
    ("10 Customer statement PDF", "Gupta Traders ka statement PDF bana ke bhejo.", None),
    ("11 Business summary", "Business ka summary do: kul kitna baaki hai aur kitna overdue?", None),
]
_orig_read_output = Sandbox.read_output


def _read_output(self, mid, name):
    hits = list(Path(_DIR).glob(f"*/{mid}"))                      # a kept owner file: local:<token>:<name>
    return hits[0].read_bytes() if hits else _orig_read_output(self, mid, name)


Sandbox.read_output = _read_output


async def _fetch_media(app, msg):
    return h.FILES[msg.media_id][1], msg.mime or "application/octet-stream"


owner_flow.fetch_media = _fetch_media


async def seed(pool):
    async with pool.connection() as c:
        await c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
    await migrate(pool)
    t = await tenants.create(pool, name="Mehta Distributors", owner_phone=OWNER, upi_vpa="mehta@upi")
    async with pool.connection() as c:
        await c.execute("update tenants set owner_last_inbound_at = now()")
    return t


def lines(m: dict) -> str:
    kind = m.get("type")
    if kind == "text":
        return m["text"]["body"]
    if kind == "interactive":
        body = m["interactive"]["body"]["text"]
        buttons = [b["reply"]["title"] for b in m["interactive"].get("action", {}).get("buttons", [])]
        return f"[CARD] {body}  [{' | '.join(buttons)}]"
    if kind == "document":
        return f"[FILE SENT] {m['document'].get('filename', '')}"
    return f"[{kind}]"


async def tap(env, step, n=4) -> int:
    """Press Approve for the owner on whatever is waiting (a card, a ledger change), like the owner would."""
    taps = 0
    for i in range(n):
        waiting = await q(env, "select id, pending from agent_sessions where status = 'WAITING_APPROVAL'")
        if not waiting:
            break
        p = waiting[0]["pending"]
        button = f"approve:{p['adjustment_id']}" if p.get("adjustment_id") else f"ag_ok:{waiting[0]['id']}"
        await dispatch(env.app, InboundMessage(wamid=f"tap-{step}-{i}", sender=OWNER, kind="interactive", button_id=button))
        taps += 1
    return taps


async def ev_rehearsal(env):
    env.report = []
    for n, (label, text, file_key) in enumerate(STEPS):
        before = len(env.wa.sent)
        started = time.monotonic()
        await dispatch(env.app, owner(text, f"demo-{n}", file_key))
        taps = await tap(env, n)
        secs = time.monotonic() - started
        replies = [lines(m) for m in env.wa.sent[before:] if m.get("to") == OWNER]
        env.report.append({"step": label, "secs": secs, "taps": taps, "replies": replies})
        print(f"\n=== {label}   ({secs:.0f}s, {taps} approval tap{'s' if taps != 1 else ''})\nOWNER: {text}"
              + (f"   [sends {h.FILES[file_key][0]}]" if file_key else ""), flush=True)
        for r in replies:
            print("SMARA:", r.replace("\n", "\n       "), flush=True)
    print(f"\nTOTAL {sum(r['secs'] for r in env.report):.0f}s of Smara working time over {len(STEPS)} steps", flush=True)


async def c_rehearsal(env):
    f = demo.FIGURES
    ob = {x["invoice_ref"]: x for x in await q(env, "select invoice_ref, amount_paise, state from obligations")}
    sale = [x for k, x in ob.items() if k.startswith("S-")]
    phones = {x["canonical_name"]: x["whatsapp_phone"] for x in await q(env, "select canonical_name, whatsapp_phone from parties")}
    got = {x["invoice_ref"]: x["amount_paise"] for x in await q(env, "select invoice_ref, amount_paise from obligations where party_id in "
                                                                     "(select id from parties where canonical_name = 'Gupta Traders')")}
    pdfs = [u[0] for u in env.wa.uploads if u[0].lower().endswith(".pdf")]
    checks = {
        "10 invoices imported": sum(1 for k in ob if k.startswith("B-")) == f["invoices"],
        "sale recorded (12,000)": len(sale) == 1 and sale[0]["amount_paise"] == 1_200_000,
        "numbers saved": phones.get("Chopra Sons") == "919811144444" and phones.get("Dutta Agencies") == "919822255555",
        "cash applied oldest-first": got.get("B-125") == 700_000 and got.get("B-131") == 780_000,
        "Gupta total shown as 14,800": any("14,800" in r or "14800" in r.replace(",", "") for r in env.report[6]["replies"]),
        "GST report and statement PDFs sent": len(pdfs) >= 2,
        "purchase bills not in receivables": all(not k.startswith(("K-", "D-", "R-", "S-3")) for k in ob),
    }
    for k, v in checks.items():
        print(f"{'OK  ' if v else 'FAIL'} {k}", flush=True)
    return all(checks.values()), "; ".join(k for k, v in checks.items() if not v) or "all checks passed"


if __name__ == "__main__":
    h.TASKS[:] = [Task("demo_rehearsal", ("custom", ev_rehearsal), c_rehearsal)]
    h.seed = seed
    asyncio.run(h.main(), loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))
