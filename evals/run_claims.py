"""Score the debtor-claim extractor on the Hinglish set.

    python -m evals.run_claims            # heuristic baseline (no network)
    python -m evals.run_claims --llm      # also score the LLM path (needs LLM_* env)
    python -m evals.run_claims --show     # list every miss

Reports per-field accuracy and, because the owner gate protects the money, the
two error types that matter: a wrong *amount* (would mislead the owner card)
and a claim invented from chatter (spurious card).
"""
import argparse
import asyncio
import os
import selectors
import sys
from collections import Counter
from datetime import date

from evals.hinglish_claims import CASES, OUTSTANDING_RUPEES, TODAY
from src.ingest.extract import extract_claim
from src.loop.llm import LLM


class _Off:
    enabled = False


async def score(llm, show: bool) -> dict:
    today, out = date.fromisoformat(TODAY), OUTSTANDING_RUPEES * 100
    n = Counter()
    misses = []
    for text, want_type, want_amt, want_date in CASES:
        c = await extract_claim(llm, text, outstanding_paise=out, today=today)
        got_type = c.type if c else None
        got_amt = c.amount_paise // 100 if c and c.amount_paise else None
        got_date = c.date.isoformat() if c and c.date else None
        n["total"] += 1
        n["type_ok"] += got_type == want_type
        if want_type is None:
            n["chatter"] += 1
            n["spurious_card"] += got_type is not None
        else:
            n["claims"] += 1
            n["missed_claim"] += got_type is None
        if want_amt is not None:
            n["amt_expected"] += 1
            n["amt_ok"] += got_amt == want_amt
        if want_type == "DISCOUNT_CLAIM":
            n["wrong_amount"] += got_amt is not None and got_amt != want_amt
        if want_date is not None:
            n["date_expected"] += 1
            n["date_ok"] += got_date == want_date
        if want_type == "PROMISE_DATE_CHANGE":
            n["wrong_date"] += got_date is not None and got_date != want_date
        if show and (got_type, got_amt, got_date) != (want_type, want_amt, want_date):
            misses.append(f"  {text!r}\n    want {(want_type, want_amt, want_date)}  got {(got_type, got_amt, got_date)}")
    return {"n": n, "misses": misses}


def report(name: str, r: dict) -> None:
    n = r["n"]
    pct = lambda a, b: f"{100 * n[a] / n[b]:5.1f}%  ({n[a]}/{n[b]})" if n[b] else "n/a"
    print(f"\n== {name}")
    print(f"claim type correct        {pct('type_ok', 'total')}")
    print(f"discount amount correct   {pct('amt_ok', 'amt_expected')}")
    print(f"promise date correct      {pct('date_ok', 'date_expected')}")
    print(f"missed real claims        {pct('missed_claim', 'claims')}")
    print(f"spurious cards on chatter {pct('spurious_card', 'chatter')}")
    print(f"WRONG amounts proposed    {n['wrong_amount']}   WRONG dates proposed {n['wrong_date']}")
    for m in r["misses"]:
        print(m)


async def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--llm", action="store_true")
    ap.add_argument("--show", action="store_true")
    a = ap.parse_args()
    report("heuristic (no LLM)", await score(_Off(), a.show))
    if a.llm:
        env = os.environ
        llm = LLM(env["LLM_BASE_URL"], env.get("LLM_API_KEY", ""), env.get("LLM_MODEL", "glm5.3-flash"))
        report(f"LLM {env.get('LLM_MODEL', 'glm-5.3-flash')}", await score(llm, a.show))
        await llm.aclose()


if __name__ == "__main__":
    sys.path.insert(0, ".")
    asyncio.run(main(), loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))
