"""tds_check: the owner's rules in, the arithmetic out. Both models got this task badly and confidently wrong (a TDS 'due' of Rs 32.9 lakh and
Rs 3.2 lakh against the true 40.0 lakh) when they wrote the code themselves."""
import asyncio
from types import SimpleNamespace

from evals import ca_data as cd
from src.agent import actions, ca_reports, documents, extras, tds_check as tc
from src.agent.policy import SessionCtx, pre_tool_use
from src.agent.tools import TOOL_NAMES, bounded

RATES = {"194C": {"Individual": 1, "Company": 2}, "194J": 10, "194I": 10, "194H": 2}


def rows():
    f, t = cd.tds()
    return tc.parse_file("vendor_payments_tds.xlsx", f["vendor_payments_tds.xlsx"]), t


def test_the_planted_payments_check_out_to_the_independent_key():
    p, t = rows()
    res = tc.check(p["rows"], RATES, {"194C": 30000}, 20)
    assert sum(x["due"] for x in res["rows"]) / 100 == t["expected_total"] and sum(x["deducted"] for x in res["rows"]) / 100 == t["deducted_total"]
    assert sorted(x["voucher"] for x in res["short"]) == sorted(t["short_vouchers"]) and sum(x["diff"] for x in res["short"]) / 100 == t["short_total"]
    assert len(res["excess"]) == t["excess_n"] and not res["no_rate"]


def test_the_threshold_comes_first_a_missing_pan_raises_the_rate_and_halves_round_up():
    mk = lambda **k: {"row": 2, "voucher": "V", "date": "", "vendor": "A", "vendor_type": "Company", "pan": "", "section": "194C", "amount": 3_000_000, "deducted": 0, **k}
    res = tc.check([mk(amount=3_000_000), mk(amount=3_000_100), mk(section="194H", amount=412_500, pan="AAAPA1111A"), mk(section="194Q", amount=100)], RATES, {"194C": 30000}, 20)
    assert [x["due"] for x in res["rows"]] == [0, 600_000, 8_300] and [x["voucher"] for x in res["no_rate"]] == ["V"]      # 30,001 x 20% (no PAN) = 6,000.2 -> 6,000; 4,125 x 2% = 82.5 -> 83
    assert res["rows"][1]["why"] == "20% (no PAN)"


def test_a_rate_table_keyed_the_way_the_owner_said_it_covers_every_type_in_the_key():
    """The owner says 'Individual/HUF 1%, Company 2%'; the agent used that as the key and 69 payments were left out of the totals (a connected demo run)."""
    p, t = rows()
    for key in ("Individual/HUF", "individual, huf", "Individual & HUF", "Individual or HUF"):
        res = tc.check(p["rows"], {"194C": {key: 1, "Company": 2}, "194J": 10, "194I": 10, "194H": 2}, {"194C": 30000}, 20)
        assert not res["no_rate"], key
        assert sum(x["due"] for x in res["rows"]) / 100 == t["expected_total"], key
    res = tc.check(p["rows"], {"194C": {"Individuals": 1, "Company": 2}, "194J": 10, "194I": 10, "194H": 2}, {"194C": 30000}, 20)
    assert not res["no_rate"]                                                    # a close spelling of the type ('Individuals') still matches


def test_a_type_with_no_rate_is_listed_with_the_real_reason_and_a_catch_all_covers_the_rest():
    p, t = rows()
    only_individuals = {"194C": {"Individual": 1}, "194J": 10, "194I": 10, "194H": 2}
    res = tc.check(p["rows"], only_individuals, {"194C": 30000}, 20)
    assert res["no_rate"] and all("vendor type" in x["why"] and "194C" in x["why"] and "Individual" in x["why"] for x in res["no_rate"])
    lines = tc.summary_lines(res, {"summary": "x"})
    assert any("left OUT" in ln and "incomplete" in ln and "vendor type" in ln for ln in lines)       # never a silent short total, never "the file has no vendor type"
    res_all = tc.check(p["rows"], {"194C": {"Individual": 1, "Others": 2}, "194J": 10, "194I": 10, "194H": 2}, {"194C": 30000}, 20)
    assert not res_all["no_rate"]
    nothing = tc.check(p["rows"], {"194J": 10}, {}, 20)
    assert all(x["why"].startswith("no rate was given for section") for x in nothing["no_rate"])


def test_the_tool_asks_for_the_rates_instead_of_guessing_and_its_result_arrives_whole(monkeypatch):
    f, t = cd.tds()
    files = {"p1": ("vendor_payments_tds.xlsx", f["vendor_payments_tds.xlsx"])}

    async def fetch(app, ref, ctx=None):
        n, d = files[ref]
        return d, "", n
    sent = []

    async def send(app, ctx, args):
        sent.append(args["file_ref"])
        return {"sent": True}

    async def make(app, ctx, args):
        return {"file_ref": "sandbox:j:r.pdf", "filename": "r.pdf", "_new_files": ["sandbox:j:r.pdf"]}

    async def excel(app, name, sheets):
        assert [s[0] for s in sheets] == ["Short deducted", "Deducted too much", "All payments", "Rules used"]
        return f"sandbox:j:{name}", name
    monkeypatch.setattr(actions, "_fetch", fetch)
    monkeypatch.setattr(extras, "send_file_to_owner", send)
    monkeypatch.setattr(documents, "make_document", make)
    monkeypatch.setattr(ca_reports, "excel_file", excel)
    ctx = SessionCtx("s", "t", "TRUSTED", files=tuple(files))
    app = SimpleNamespace()
    assert "do not guess" in asyncio.run(tc.tds_check(app, ctx, {"file_ref": "p1"}))["error"]
    r = asyncio.run(tc.tds_check(app, ctx, {"file_ref": "p1", "rates": RATES, "thresholds": {"194C": 30000}, "no_pan_rate": 20}))
    assert len(sent) == 2 and r["short_count"] == t["short_n"] and r["expected_tds_rupees"] == t["expected_total"] and list(r)[0] == "summary_lines" and "[truncated]" not in bounded(r)
    assert any("Rs 40,03,845.00" in x for x in r["summary_lines"]) and "194C Individual 1%" in r["rules_used"] and "never recompute" in r["next"]
    assert "tds_check" in TOOL_NAMES and pre_tool_use(ctx, "tds_check", {"file_ref": "p1", "rates": RATES}, TOOL_NAMES).verdict == "allow"
    assert pre_tool_use(SessionCtx("s", "t", "UNTRUSTED", party_id="p"), "tds_check", {"file_ref": "p1", "rates": RATES}, TOOL_NAMES).verdict == "deny"
