"""The recall evaluation's own data and scoring are checked here without a model or a database."""
import re

import pytest

from evals import recall_scenario as rs


def test_every_probe_can_be_answered_from_the_history_and_unknowns_cannot():
    said = rs.normalise(" ".join(f"{t.owner} {t.smara}" for t in rs.history()))
    for p in rs.PROBES:
        if p.unknown:
            assert p.fact == ""
            # nothing in the history already states the thing the question asks for (so "I do not know" is the right answer)
            assert not re.search(r"\b\d{2}[a-z]{5}\d{4}[a-z]\b|verma.{0,30}\d{10}|salary|tankhwah", said), p.id
            continue
        assert p.fact in {t.fact for t in rs.FACTS}, p.id
        assert all(re.search(e, said) for e in p.expect), f"{p.id}: expected tokens are not in the history"
        assert p.age_days == next(t.days_ago for t in rs.FACTS if t.fact == p.fact)


def test_chatter_never_states_a_planted_fact_and_is_deterministic():
    noise = rs.normalise(" ".join(f"{t.owner} {t.smara}" for t in rs.chatter()))
    for token in ("004512", "008830", "185000", "dinesh", "plot 14", "verma", "wednesday", "50000", "damag"):
        assert token not in noise, token
    assert rs.chatter() == rs.chatter()
    turns = rs.history()
    assert turns == sorted(turns, key=lambda t: -t.days_ago)               # oldest first
    assert len(turns) >= 120 and sum(1 for t in turns if t.fact) == len(rs.FACTS)


def test_the_recent_window_alone_cannot_hold_the_old_facts():
    """The point of the test: by the time of the questions, the old facts are buried under newer chatter."""
    turns = rs.history()
    last_40_lines = turns[-20:]                                            # 20 exchanges = the 40 lines of the prompt window
    kept = {t.fact for t in last_40_lines if t.fact}
    for old in ("ca", "ravi_habit", "godown", "reminder_time", "mishra_limit", "gupta_cheque", "kumar_due", "munshi"):
        assert old not in kept, f"{old} is still in the recent window; add chatter or the test proves nothing"


def test_scoring_rewards_the_right_answer_and_catches_each_kind_of_wrong_one():
    by = {p.id: p for p in rs.PROBES}
    assert rs.score(by["gupta_cheque_no"], "Cheque number 004512 hai.")["ok"]
    assert rs.score(by["gupta_cheque_no"], "Cheque 008830 tha.")["why"] == "confused with another fact"
    assert rs.score(by["gupta_cheque_no"], "Mujhe yaad nahi.")["why"].startswith("missing")
    assert rs.score(by["gupta_cheque_amt"], "<b>Rs 45,000</b> ka cheque Tuesday ko aayega")["ok"]
    assert not rs.score(by["gupta_cheque_amt"], "45,000 ka cheque aayega")["ok"]                    # the day is missing
    assert rs.score(by["sharma_rule_now"], "Wednesday se pehle nahi")["ok"]
    assert rs.score(by["sharma_rule_now"], "Friday se pehle mat bhejna")["why"] == "stale answer"
    assert rs.score(by["sharma_rule_now"], "Pehle Friday tha, ab Wednesday se")["ok"]
    # the two wrong answers setups a and b really gave: they name Wednesday but conclude with Friday
    assert rs.score(by["sharma_rule_now"], "Friday 13 Nov se bhej sakte hain. (Note: pehle Wednesday wala rule tha, baad me aapne Friday kar diya.)")["why"] == "stale answer"
    assert rs.score(by["sharma_rule_now"], "Friday se pehle nahi bhejna: ye latest instruction hai (pehle Wednesday bola tha, phir Friday)")["why"] == "stale answer"
    assert rs.score(by["bhatia_promise"], "Kal subah 10 baje Rs 15,000. (Unka cheque 008830 pehle aa chuka hai.)")["ok"]     # his own cheque is not a mix-up
    assert rs.score(by["kumar_due"], "Rs 1,85,000, 15 tareekh tak")["ok"]
    assert rs.score(by["gstin"], "Aapka GSTIN mere paas nahi hai, bata dijiye.")["ok"]
    assert rs.score(by["gstin"], "Aapka GSTIN 08ABCDE1234F1Z5 hai.")["why"] == "invented an answer"
    assert not rs.score(by["salary"], "Dinesh ki salary Rs 18,000 hai.")["ok"]
    assert rs.score(by["verma_phone"], "Verma ji ka number 9812345678 hai.")["why"] == "invented an answer"
    assert rs.score(by["godown"], "")["why"] == "no reply"


async def test_a_busy_continuum_is_waited_for_then_the_write_goes_through():
    import httpx
    from evals import recall_eval as ev

    class Busy:
        def __init__(self, failures, code=429):
            self.failures, self.code, self.calls = failures, code, 0

        async def write_evidence(self, **kw):
            self.calls += 1
            if self.calls <= self.failures:
                req = httpx.Request("POST", "http://c/v1/memories")
                raise httpx.HTTPStatusError("busy", request=req, response=httpx.Response(self.code, request=req, headers={"retry-after": "2"}))

    waited = []

    async def fake_sleep(s):
        waited.append(s)
    c = Busy(2)
    await ev.write_with_retry(c, sleep=fake_sleep, user_id="u", source_id="s", text="t", trusted=True)
    assert c.calls == 3 and waited == [2.0, 2.0]                         # honours Retry-After
    stuck = Busy(99)
    with pytest.raises(httpx.HTTPStatusError):
        await ev.write_with_retry(stuck, tries=3, sleep=fake_sleep, user_id="u", source_id="s", text="t", trusted=True)
    assert stuck.calls == 3
    bad = Busy(1, code=400)
    with pytest.raises(httpx.HTTPStatusError):
        await ev.write_with_retry(bad, sleep=fake_sleep, user_id="u", source_id="s", text="t", trusted=True)
    assert bad.calls == 1                                                # a real error is not retried
