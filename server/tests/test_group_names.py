"""mh.group_names: spellings that differ only in case, spaces, punctuation, M/s or Messrs are one party; look-alikes are listed for a person."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sandbox"))
import smara_helpers as mh  # noqa: E402


def test_obvious_variants_are_one_group_and_look_alikes_are_only_suggested():
    names = ["Apex Steel", "APEX STEEL", "apex steel ", "M/s Apex Steel", "Messrs Apex Steel", "Apex Steel.", "Zenith Traders", "Zenith Trading Co", "Orion Agencies", "Orion Agency", "Apex Foods"]
    g = mh.group_names(names)
    apex = next(x for x in g["groups"] if x["key"] == "apex steel")
    assert apex["name"] == "Apex Steel" and apex["rows"] == 6 and len(apex["spellings"]) == 6
    assert len(g["groups"]) == 6                                                          # apex steel, apex foods, zenith x2, orion x2
    pairs = {frozenset((p["a"], p["b"])) for p in g["maybe_same"]}
    assert frozenset(("Zenith Traders", "Zenith Trading Co")) in pairs and frozenset(("Orion Agencies", "Orion Agency")) in pairs
    assert not any("Apex Foods" in p for p in pairs)


def test_totals_by_group_do_not_lose_or_double_a_row():
    rows = [("M/s Kapoor Traders", 100), ("kapoor traders", 50), ("Kapoor Traders", 25), ("Sharma Store", 10)]
    g = mh.group_names([r[0] for r in rows])
    key_of = {s: x["name"] for x in g["groups"] for s in x["spellings"]}
    tot = {}
    for n, a in rows:
        tot[key_of[n]] = tot.get(key_of[n], 0) + a
    assert tot == {"Kapoor Traders": 175, "Sharma Store": 10}
