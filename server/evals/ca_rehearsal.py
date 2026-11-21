"""Rehearse the accountant demo as ONE conversation with one shop: the real model, the real agent loop, scratch Postgres, fake WhatsApp.

    python -m evals.ca_rehearsal --db postgresql://.../smara_eval --model glm5.3 --sandbox container --jobs /jobs --bills /data/bills

The steps are tasks of evals/ca_eval.py (the same messages, the same made-up files), played in the order of a demo, in one chat, so the agent
carries the whole conversation with it, the way it does in front of a client. Every step prints what the owner sent, what the agent answered,
the files it sent and the time; then that step's checker runs on this step's output only (the fake WhatsApp log is emptied between steps; the
agent still remembers the conversation). A checker can be lenient about leftovers of earlier steps (for example the count of questions asked),
so READ THE REPLIES: the printout is the point, the checks are a safety net.
The harness DROPs the public schema: the database name must end in _eval. Make the bills first: python -m evals.bill_data --out DIR.
"""
import argparse
import asyncio
import selectors
import sys
import time

from evals import agent_harness as h
from evals import ca_eval as ce
from evals import demo_scenarios as ds
from evals.agent_harness import OWNER, Task, q
from src.ingest.dispatch import dispatch
from src.ingest.payload import InboundMessage

ORDER = [
    ("1  GST: purchase register vs GSTR-2B", "gst_2b_reconciliation"),
    ("2  Bank book vs bank statement", "bank_reconciliation_statement"),
    ("3  Receivables ageing, party-wise", "receivables_ageing"),
    ("4  TDS check with the owner's rules", "tds_short_deduction"),
    ("5  Salary sheet: add PF, ESI, net pay", "payroll_pf_esi"),
    ("6  A phone photo of the electricity bill", "bill_phone_photo_pay_and_due"),
    ("7  A scanned PDF bill", "bill_scanned_pdf_pay_and_due"),
    ("8  Is anything wrong on this bill?", "bill_with_a_wrong_charge"),
    ("9  An unclear order: does it ask?", "ambiguous_amount_column"),
    ("10 A standing rule, then a bill", "learns_standing_bill_reminder"),
    ("11 Reply language, said once", "learns_reply_language"),
]
TASKS: dict = {}


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


async def tap(env, step: int, n: int = 4) -> int:
    taps = 0
    for i in range(n):
        waiting = await q(env, "select id, pending from agent_sessions where status = 'WAITING_APPROVAL'")
        if not waiting:
            break
        p = waiting[0]["pending"]
        button = f"approve:{p['adjustment_id']}" if p.get("adjustment_id") else f"ag_ok:{waiting[0]['id']}"
        await dispatch(env.app, InboundMessage(wamid=f"rh-tap-{step}-{i}", sender=OWNER, kind="interactive", button_id=button))
        taps += 1
    return taps


async def ev_rehearsal(env):
    env.report = []
    for n, (label, name) in enumerate(ORDER):
        task = TASKS[name]
        env.wa.sent.clear()
        env.wa.uploads.clear()
        started = time.monotonic()
        try:
            kind, ev = task.event
            if kind == "custom":
                await ev(env)
            else:
                await dispatch(env.app, ev)
            taps = await tap(env, n) if task.approve else 0
        except Exception as e:                       # one broken step must not hide the others
            taps = 0
            print(f"\n!!! step {label} raised {type(e).__name__}: {e}", flush=True)
        secs = time.monotonic() - started
        try:
            ok, detail = await task.check(env)
        except Exception as e:
            ok, detail = False, f"checker raised {type(e).__name__}: {e}"
        replies = [lines(m) for m in env.wa.sent if m.get("to") == OWNER]
        files = [u[0] for u in env.wa.uploads]
        env.report.append({"step": label, "name": name, "ok": ok, "secs": secs, "detail": detail})
        print(f"\n=== {label}   ({secs:.0f}s)   check: {'PASS' if ok else 'FAIL'}" + ("" if ok else f"  <- {str(detail)[:400]}"), flush=True)
        for r in replies:
            print("SMARA:", r.replace("\n", "\n       "), flush=True)
        if files:
            print("FILES SENT:", ", ".join(files), flush=True)
    print(f"\nTOTAL {sum(r['secs'] for r in env.report):.0f}s of working time over {len(ORDER)} steps; "
          f"{sum(r['ok'] for r in env.report)}/{len(ORDER)} checks pass", flush=True)


async def c_standing_rule_in_a_chat(env):
    """In one long chat the owner has already sent other bills, and the agent may sensibly set their reminders too when the rule arrives; so here the test
    is that the NEW bill got its reminder on the right day, in the morning, and the owner was not asked again (not that it is the only reminder)."""
    rows = await ce.followups(env)
    want = ce.date.fromisoformat(ce.BILLS["A"]["due"]) - ce.timedelta(days=2)
    mine = [r for r in rows if ce.ist(r["run_at"]).date() == want and 8 <= ce.ist(r["run_at"]).hour < 12]
    asked = getattr(env, "asked_second", None)
    return bool(mine) and not asked, f"{len(rows)} reminders in all, {len(mine)} on {want:%d %b} in the morning; asked again: {asked}"


async def c_rehearsal(env):
    bad = [r["step"] for r in env.report if not r["ok"]]
    return not bad, "all steps pass their check" if not bad else "FAILED: " + "; ".join(bad)


if __name__ == "__main__":
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--bills", default="")
    known, rest = pre.parse_known_args()
    if known.bills:
        ce.load_bills(known.bills)
    TASKS.update({t.name: t for t in ce.build_tasks()})
    missing = [n for _, n in ORDER if n not in TASKS]
    if missing:
        sys.exit(f"steps not found (are the bills loaded? --bills DIR): {missing}")
    TASKS["learns_standing_bill_reminder"].check = c_standing_rule_in_a_chat
    task = Task("ca_rehearsal", ("custom", ev_rehearsal), c_rehearsal)
    task.group, task.budget = "rehearsal", None
    h.TASKS[:] = [task]
    h.seed = ds.seed
    h.run_task = ce.timed_run_task
    from src.config import Settings as _Cfg      # demo_scenarios already points h.Settings at a zero-argument lambda: build it again here
    h.Settings = lambda: _Cfg(owner_files_dir=ds._DIR, quiet_start_hour=21, quiet_end_hour=8)
    sys.argv = [sys.argv[0]] + rest
    asyncio.run(h.main(), loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))
