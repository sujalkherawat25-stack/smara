"""Regression floor for the offline claim extractor on the Hinglish set.

The set is hand-written by the same people who tuned the rules, so 100% here is
an optimistic ceiling, not a production accuracy. It guards against regressions,
and the hard rule: never propose a wrong amount or date.
"""
from datetime import date

from evals.hinglish_claims import CASES
from evals.run_claims import _Off, score


async def test_hinglish_floor():
    n = (await score(_Off(), show=False))["n"]
    assert n["total"] == len(CASES) >= 110
    assert n["wrong_amount"] == 0 and n["wrong_date"] == 0
    assert n["type_ok"] / n["total"] >= 0.95
    assert n["spurious_card"] / n["chatter"] <= 0.05
    assert n["amt_ok"] / n["amt_expected"] >= 0.9 and n["date_ok"] / n["date_expected"] >= 0.9


def test_labels_are_consistent():
    types = {t for _, t, _, _ in CASES}
    assert types == {None, "DISCOUNT_CLAIM", "PROMISE_DATE_CHANGE", "UTR_PROOF"}
    for text, t, amt, when in CASES:
        assert amt is None or t == "DISCOUNT_CLAIM", text
        assert when is None or t in ("PROMISE_DATE_CHANGE", "DISCOUNT_CLAIM"), text
        assert when is None or date.fromisoformat(when)
    assert len({text for text, *_ in CASES}) == len(CASES), "duplicate messages"
