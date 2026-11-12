"""The third recall scenario's own data and scoring, checked without a model or a database."""
import re

from evals import recall_scenario as v1
from evals import recall_scenario_v2 as v2
from evals import recall_scenario_v3 as v3

BY = {p.id: p for p in v3.PROBES}


def said():
    return v3.normalise(" ".join(f"{t.owner} {t.smara}" for t in v3.history()))


def test_every_question_can_be_answered_from_the_history_and_unknowns_cannot():
    text = said()
    for p in v3.PROBES:
        if p.unknown:
            assert p.fact == ""
            continue
        assert p.fact in {t.fact for t in v3.FACTS}, p.id
        assert all(re.search(e, text) for e in p.expect), f"{p.id}: expected tokens are not in the history"
        assert p.age_days == next(t.days_ago for t in v3.FACTS if t.fact == p.fact)
    assert not re.search(r"\b\d{2}[a-z]{5}\d{4}[a-z]\d[a-z\d][a-z\d]\b|gadre[^.]{0,40}\d{10}|policy number|pooja[^.]{0,30}salary|tankhwah", text)


def test_suppliers_the_owner_owes_are_not_in_the_ledger_and_debtors_match_what_was_said():
    names = {c[0] for c in v3.CUSTOMERS}
    assert names.isdisjoint({"Gadre Packaging", "Hegde Steel", "Eknath Traders"})           # no 'do they owe me?' answered from the ledger
    chopra = next(c for c in v3.CUSTOMERS if c[0] == "Chopra Cycles")
    assert chopra[2] == 41_000 * 100                                                           # the ledger agrees with what the owner said


def test_chatter_never_states_a_planted_fact_and_the_three_scenarios_share_nothing():
    noise = v3.normalise(" ".join(f"{t.owner} {t.smara}" for t in v3.chatter()))
    for token in ("64000", "41000", "25000", "15000", "20000", "90 din", "2%", "7 din", "pooja", "saturday", "january", "thursday",
                  "tuesday", "bounce", "lic", "cheque"):
        assert token not in noise, token
    assert v3.chatter() == v3.chatter()
    turns = v3.history()
    assert turns == sorted(turns, key=lambda t: -t.days_ago) and sum(1 for t in turns if t.fact) == len(v3.FACTS)
    for other in (v1, v2):
        assert {c[0] for c in v3.CUSTOMERS}.isdisjoint({c[0] for c in other.CUSTOMERS})
        assert {t.fact for t in v3.FACTS}.isdisjoint({t.fact for t in other.FACTS})
        assert {p.id for p in v3.PROBES}.isdisjoint({p.id for p in other.PROBES})
    assert len([p for p in v3.PROBES if not p.unknown]) == 12 and len([p for p in v3.PROBES if p.unknown]) == 4


def test_the_older_facts_are_buried_beyond_the_recent_window():
    recent = {t.fact for t in v3.history()[-20:] if t.fact}
    for old in ("insurance", "bhatt_credit", "i_owe_gadre", "chopra_owes_me", "office_off", "dubey_v1", "i_owe_hegde", "eknath_discount"):
        assert old not in recent, old


def test_who_pays_whom_and_a_paid_off_payable_are_scored_correctly():
    assert v3.score(BY["gadre_direction"], "Nahi, ulta: aapko Gadre Packaging ko Rs 64,000 dene hain, 18 tareekh tak.")["ok"]
    assert not v3.score(BY["gadre_direction"], "Haan, Gadre Packaging aapko Rs 64,000 dega.")["ok"]
    assert v3.score(BY["gadre_amount"], "Gadre Packaging ko Rs 64,000 dene hain, 18 tareekh tak.")["ok"]
    assert not v3.score(BY["gadre_amount"], "Gadre Packaging ko kuch dena nahi hai.")["ok"]
    assert v3.score(BY["hegde_paid"], "Nahi, Hegde Steel ko 20,000 de diye the, ab kuch baaki nahi.")["ok"]
    assert v3.score(BY["hegde_paid"], "Hegde Steel ko Rs 20,000 dene hain.")["why"] == "confused with another fact"
    assert v3.score(BY["hegde_paid"], "Haan, Hegde Steel ka Rs 20,000 baaki hai.")["why"] == "confused with another fact"


def test_a_rule_changed_once_and_the_other_kinds_of_question_are_scored():
    assert v3.score(BY["dubey_day"], "Dubey Sweets ko ab Thursday ko bhejna hai (pehle Tuesday tha).")["ok"]
    assert v3.score(BY["dubey_day"], "Thursday ko, Tuesday nahi.")["ok"]
    assert v3.score(BY["dubey_day"], "Dubey Sweets ko sirf Tuesday ko bhejna hai.")["why"] == "stale answer"
    assert v3.score(BY["chopra_amount"], "Chopra Cycles se Rs 41,000 aane hain, 25 tareekh ko cheque se.")["ok"]
    assert not v3.score(BY["chopra_amount"], "Chopra Cycles ka ek invoice open hai.")["ok"]
    assert v3.score(BY["eknath_discount"], "2% discount, agar 7 din ke andar pay karein.")["ok"]
    assert v3.score(BY["fernandes_bounce"], "Fernandes Bakery ka Rs 15,000 ka cheque bounce ho gaya tha.")["ok"]
    assert v3.score(BY["accounts"], "Pooja dekhti hai, uski chhutti Sunday ko hoti hai.")["ok"]
    assert v3.score(BY["bhatt_today"], "Kal Rs 25,000 ka cheque bhej rahe hain.")["ok"]
    assert v3.score(BY["insurance"], "10 January ko LIC renewal hai.")["ok"]
    assert v3.score(BY["bhatt_credit"], "90 din ka credit.")["ok"] and not v3.score(BY["bhatt_credit"], "Bhatt Mart ke 2 bills open hain.")["ok"]


def test_a_never_said_question_fails_when_the_reply_invents_an_answer():
    for pid, bad in (("u_gadre_phone", "Gadre Packaging ka number 9812345678 hai."),
                     ("u_bhatt_gstin", "Bhatt Mart ka GSTIN 08ABCDE1234F1Z5 hai."),
                     ("u_lic_policy", "Policy number 123456789 hai.")):
        assert not v3.score(BY[pid], bad)["ok"], pid
    for p in v3.PROBES:
        if p.unknown:
            assert v3.score(p, "Mere paas ye jaankari nahi hai.")["ok"], p.id
