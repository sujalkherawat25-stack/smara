"""Sandbox helpers and runner (local mode), plus the agent doing a Tally XML import end to end."""
import io
import json
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sandbox"))
import munim_helpers as mh  # noqa: E402

from src.box.sandbox import Sandbox, safe_name  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "tally_daybook.xml"


def test_indian_amounts_and_dates():
    assert mh.parse_amount("1,25,000.50 Dr") == 125000.5
    assert mh.parse_amount("9,999 Cr") == -9999 and mh.parse_amount("(500)") == -500
    assert mh.parse_amount("Rs. 1,500") == 1500 and mh.parse_amount("-") is None
    assert mh.parse_date("05-10-2026") == date(2026, 10, 5) == mh.parse_date("20261005")
    assert mh.parse_date(46300) == date(2026, 10, 5)                       # Excel serial
    assert mh.phone("98765 43210") == "919876543210" and mh.phone("9876543210.0") == "919876543210"


def test_tally_xml_vouchers_and_ledgers():
    v = {x["number"]: x for x in mh.tally_vouchers(FIXTURE)}
    assert v["S-101"] == {"date": date(2026, 10, 5), "type": "Sales", "number": "S-101", "party": "Ravi Kirana Store",
                          "amount": 12500.0, "credit_days": 30}
    assert v["S-102"]["party"] == "Sharma & Sons" and v["S-102"]["amount"] == 4000.5
    assert v["R-7"]["type"] == "Receipt" and v["R-7"]["amount"] == 2500.0   # survives Tally's &#4; junk
    rec = {r["invoice"]: r for r in mh.tally_receivables(FIXTURE)}
    assert set(rec) == {"S-101", "S-102"}                                       # receipts excluded
    assert rec["S-101"]["phone"] == "919876543210" and rec["S-101"]["due_date"] == date(2026, 11, 4)
    assert rec["S-102"]["phone"] == "919811122233" and rec["S-102"]["due_date"] == date(2026, 10, 27)
    led = {x["name"]: x for x in mh.tally_ledgers(FIXTURE)}
    assert led["Ravi Kirana Store"]["mobile"] == "919876543210" and led["Sharma & Sons"]["closing_balance"] == -4000.5


def test_read_table_skips_title_rows(tmp_path):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    for row in (["ABC Traders"], ["Bills Receivable as on 03-11-2026"], [],
                ["Date", "Ref. No.", "Party's Name", "Pending Amount", "Due on"],
                ["05-10-2026", "S-101", "Ravi Kirana", "12,500.00 Dr", "04-11-2026"]):
        ws.append(row)
    wb.save(tmp_path / "r.xlsx")
    df = mh.read_table(tmp_path / "r.xlsx")
    assert list(df.columns)[:3] == ["Date", "Ref. No.", "Party's Name"] and df.iloc[0]["Ref. No."] == "S-101"


async def test_runner_result_outputs_errors_and_timeout(tmp_path):
    sb = Sandbox(str(tmp_path), "local")
    ok = await sb.run("import pandas as pd, csv\n"
                      "rows = list(csv.reader(open('in/a.csv')))\n"
                      "RESULT = {'n': len(rows) - 1}\n"
                      "open('out/x.txt', 'w').write('hi')\nprint('done')", {"a.csv": b"h\n1\n2\n"})
    assert ok["exit_code"] == 0 and ok["result"] == {"n": 2} and ok["outputs"] == ["x.txt"] and "done" in ok["stdout"]
    assert sb.read_output(ok["job_id"], "x.txt") == b"hi"
    bad = await sb.run("1/0", {})
    assert bad["exit_code"] == 1 and "ZeroDivisionError" in bad["stderr"]
    slow = await sb.run("while True: pass", {}, timeout_s=2)
    assert slow["timed_out"] is True
    with pytest.raises(ValueError):
        sb.read_output("../etc", "passwd")
    assert safe_name("../../evil.csv") == "evil.csv"
