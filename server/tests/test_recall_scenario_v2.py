"""The second recall scenario's own data and scoring, checked without a model or a database."""
import re

import pytest

from evals import recall_scenario as v1
from evals import recall_scenario_v2 as v2

BY = {p.id: p for p in v2.PROBES}


def said():
    return v2.normalise(" ".join(f"{t.owner} {t.smara}" for t in v2.history()))


def test_every_question_can_be_answered_from_the_history_and_unknowns_cannot():
    text = said()
    for p in v2.PROBES:
        if p.unknown:
            assert p.fact == ""
            continue
        assert p.fact in {t.fact for t in v2.FACTS}, p.id
        assert all(re.search(e, text) for e in p.expect), f"{p.id}: expected tokens are not in the history"
        assert p.age_days == next(t.days_ago for t in v2.FACTS if t.fact == p.fact)
    # nothing in the history already answers the never-said questions
    assert not re.search(r"\b\d{2}[a-z]{5}\d{4}[a-z]\d[a-z\d][a-z\d]\b|suresh[^.]{0,40}\d{10}|axis[^.]{0,60}\d{9,}|patel[^.]{0,30}gstin|meeting", text)


def test_chatter_never_states_a_planted_fact_and_is_deterministic():
    noise = v2.normalise(" ".join(f"{t.owner} {t.smara}" for t in v2.chatter()))
    for token in ("axis", "mehta", "60 din", "2%", "naram", "friday", "wednesday", "80000", "32500", "150000", "100000", "dopahar",
                  "suresh", "pappu", "27000", "hdfc", "18000", "jaipur"):
        assert token not in noise, token
    assert v2.chatter() == v2.chatter() and v2.chatter() != v1.chatter()
    turns = v2.history()
    assert turns == sorted(turns, key=lambda t: -t.days_ago) and len(turns) >= 125
    assert sum(1 for t in turns if t.fact) == len(v2.FACTS)


def test_it_is_a_different_test_not_the_first_one_reworded():
    assert {c[0] for c in v2.CUSTOMERS}.isdisjoint({c[0] for c in v1.CUSTOMERS})
    assert {t.fact for t in v2.FACTS}.isdisjoint({t.fact for t in v1.FACTS})
    assert {p.id for p in v2.PROBES}.isdisjoint({p.id for p in v1.PROBES})
    assert len([p for p in v2.PROBES if not p.unknown]) >= 15 and len([p for p in v2.PROBES if p.unknown]) == 4
    kinds = {p.kind for p in v2.PROBES}
    assert {"direction", "exception", "update", "unknown"} <= kinds                   # the harder kinds are present


def test_the_older_facts_are_buried_beyond_the_recent_window():
    recent = {t.fact for t in v2.history()[-20:] if t.fact}
    for old in ("bank_days", "gst_ca", "jain_credit", "patel_discount", "tone", "rathore_v1", "hours_shop", "khan_owes_me", "people"):
        assert old not in recent, old


def test_the_changed_rule_probes_expect_the_last_value_only():
    # three versions of one rule, and a figure corrected the same day
    rathore = [t.fact for t in sorted(v2.FACTS, key=lambda t: -t.days_ago) if t.fact.startswith("rathore")]
    assert rathore == ["rathore_v1", "rathore_v2", "rathore_v3"]
    deshmukh = [t for t in sorted(v2.FACTS, key=lambda t: -t.days_ago) if t.fact.startswith("deshmukh")]
    assert [t.fact for t in deshmukh] == ["deshmukh_v1", "deshmukh_v2"] and deshmukh[0].days_ago > deshmukh[1].days_ago
    assert v2.score(BY["rathore_day"], "Rathore Agencies ko ab Friday ko bhejna hai.")["ok"]
    assert v2.score(BY["rathore_day"], "Friday ko bhejna hai (pehle Monday tha, phir Wednesday kiya tha).")["ok"]
    assert v2.score(BY["rathore_day"], "Friday ko bhejna hai, Wednesday nahi.")["ok"]
    assert v2.score(BY["rathore_day"], "Rathore Agencies ko sirf Wednesday ko bhejna hai.")["why"] == "stale answer"
    assert v2.score(BY["rathore_day"], "Monday ko bhejna hai.")["why"] == "stale answer"
    assert v2.score(BY["deshmukh_limit"], "Deshmukh & Sons ka credit limit Rs 1,50,000 hai.")["ok"]
    assert v2.score(BY["deshmukh_limit"], "Credit limit Rs 1,00,000 hai.")["why"] == "stale answer"
    assert v2.score(BY["deshmukh_limit"], "Pehle 1,00,000 tha, ab Rs 1,50,000 hai.")["ok"]


def test_a_flipped_direction_of_who_pays_whom_is_caught():
    assert v2.score(BY["khan_direction"], "Nahi, ulta: Khan Brothers aapko Rs 80,000 denge, 20 tareekh ko.")["ok"]
    assert not v2.score(BY["khan_direction"], "Haan, aapko Khan Brothers ko Rs 80,000 dene hain.")["ok"]
    assert v2.score(BY["singh_direction"], "Nahi, aapko Singh Traders ko Rs 32,500 dene hain, 5 tareekh tak.")["ok"]
    assert not v2.score(BY["singh_direction"], "Haan, Singh Traders aapko Rs 32,500 dega.")["ok"]


def test_a_shop_rule_and_a_customers_exception_are_scored_separately():
    assert v2.score(BY["hours_shop"], "Reminders 12 baje ke baad hi jaate hain.")["ok"]
    assert not v2.score(BY["hours_shop"], "Subah 8 baje se raat 9 baje ke beech.")["ok"]
    assert v2.score(BY["singh_hours"], "Singh Traders ko 10 baje ke baad bhej sakte hain (exception).")["ok"]
    assert not v2.score(BY["singh_hours"], "Sab reminders 12 baje ke baad jaate hain.")["ok"]


def test_the_other_questions_accept_right_answers_and_refuse_vague_ones():
    assert v2.score(BY["bank_days"], "Axis bank, Monday aur Thursday ko jaate hain.")["ok"]
    assert not v2.score(BY["bank_days"], "Axis bank hai.")["ok"]
    assert v2.score(BY["gst_ca"], "GST 20 tareekh ko, CA Mehta saab (Jaipur).")["ok"]
    assert v2.score(BY["jain_credit"], "Jain Medical ko 60 din ka credit hai.")["ok"]
    assert not v2.score(BY["jain_credit"], "Jain Medical ke 2 invoices open hain.")["ok"]
    assert v2.score(BY["patel_discount"], "2% discount, agar 10 din ke andar pay kare.")["ok"]
    assert not v2.score(BY["patel_discount"], "Patel Hardware ke 2 invoices overdue hain, 10 baje bhejein.")["ok"]
    assert v2.score(BY["tone"], "Naram tone mein, hamesha 'aap' bolna.")["ok"]
    assert v2.score(BY["keys"], "Chaabi Suresh ke paas hai.")["ok"] and v2.score(BY["delivery"], "Pappu delivery dekhta hai.")["ok"]
    assert v2.score(BY["jain_cheque"], "Rs 27,000 ka HDFC ka cheque.")["ok"]
    assert v2.score(BY["patel_today"], "Kal shaam 5 baje tak Rs 18,000.")["ok"]


@pytest.mark.parametrize("pid, bad", [("u_patel_gstin", "Patel Hardware ka GSTIN 08ABCDE1234F1Z5 hai."),
                                       ("u_suresh_phone", "Suresh ka number 9812345678 hai."),
                                       ("u_axis_account", "Account number 917020012345678 hai."),
                                       ("u_singh_meeting", "Last meeting 3 hafte pehle hui thi, bahut achhi rahi.")])
def test_a_never_said_question_fails_when_the_reply_invents_an_answer(pid, bad):
    assert not v2.score(BY[pid], bad)["ok"]
    assert v2.score(BY[pid], "Mere paas ye jaankari nahi hai.")["ok"]


def test_a_history_that_ends_on_the_new_value_is_right_and_one_that_ends_on_the_old_value_is_not():
    """Real replies from the first run of this scenario: three correct answers were marked stale because they listed the
    history ("pehle Monday, phir Wednesday, ab Friday"). The one genuinely stale answer is still caught."""
    right = ["Rathore Agencies ko reminder sirf Friday ko bhejna hai, Wednesday nahi. (Aapne rule badla tha - pehle Monday, phir Wednesday, ab Friday.)",
             "Rathore Agencies ko reminder Friday ko bhejna hai. Aapne 7 Nov ko rule badla tha - pehle Monday, phir Wednesday, aur ab Friday.",
             "Ab Friday ko bhejna hai; pehle Monday tha, phir Wednesday kiya tha."]
    wrong = ["Wednesday. Aapne pehle Monday bola tha, phir rule badal kar Wednesday kar diya - ab sirf Wednesday ko jayega.",
             "Rathore Agencies ko sirf Wednesday ko bhejna hai.", "Monday ko bhejna hai, phir Wednesday ko."]
    for reply in right:
        assert v2.score(BY["rathore_day"], reply)["ok"], reply
    for reply in wrong:
        assert v2.score(BY["rathore_day"], reply)["why"] == "stale answer", reply


def test_the_history_rule_does_not_let_through_the_wrong_answers_the_first_scenario_caught():
    sharma = {p.id: p for p in v1.PROBES}["sharma_rule_now"]
    for reply in ("Friday 13 Nov se bhej sakte hain. (Note: pehle Wednesday wala rule tha, baad me aapne Friday kar diya.)",
                  "Friday se pehle nahi bhejna: ye latest instruction hai (pehle Wednesday bola tha, phir Friday)"):
        assert v1.score(sharma, reply)["why"] == "stale answer", reply
    assert v1.score(sharma, "Pehle Friday tha, ab Wednesday se")["ok"]


def test_saved_replies_can_be_rescored_without_asking_again():
    from evals import recall_eval as ev
    ev.rs = v2
    out = {"model": "m", "scenario": "v2", "repeats": 1, "questions": 1, "systems": [
        {"system": "a", "label": "x", "setup_secs": 0, "results": [
            {"probe": "rathore_day", "kind": "update", "age_days": 5, "ok": False, "why": "stale answer", "secs": 1, "tokens": 1,
             "reply": "Friday ko bhejna hai (pehle Monday, phir Wednesday, ab Friday)."}]}]}
    again = ev.rescore(out)
    assert again["systems"][0]["results"][0]["ok"] is True and again["rescored"] is True
    assert "Re-scored with the current scorer" in ev.summarise(again)
