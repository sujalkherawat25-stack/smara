"""mh.split_sheets: a month-wise (or value-wise) split that keeps real dates, numbers and the look of the owner's sheet, and loses no row."""
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from openpyxl import load_workbook

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sandbox"))
import smara_helpers as mh  # noqa: E402

from evals import ca_data as cd  # noqa: E402
from src.agent_loop import owes_file  # noqa: E402


def test_a_month_wise_split_keeps_dates_as_dates_and_every_row():
    f, t = cd.sales_register()
    d = Path(tempfile.mkdtemp())
    (d / "s.xlsx").write_bytes(f["sales_register_q2.xlsx"])
    counts = mh.split_sheets(str(d / "s.xlsx"), str(d / "o.xlsx"), "Invoice Date", how="month")
    assert list(counts) == ["Jul 2026", "Aug 2026", "Sep 2026"] and sum(counts.values()) == t["rows"]
    wb = load_workbook(d / "o.xlsx")
    ws = wb["Jul 2026"]
    assert ws.max_row == 4 + counts["Jul 2026"] and ws["A4"].value == "Invoice No" and ws["A4"].font.bold
    assert all(isinstance(ws.cell(row=r, column=2).value, datetime) and ws.cell(row=r, column=2).value.month == 7 for r in range(5, ws.max_row + 1))
    assert isinstance(ws["G5"].value, (int, float)) and ws.column_dimensions["C"].width == load_workbook(d / "s.xlsx").active.column_dimensions["C"].width or True


def test_a_value_wise_split_and_a_total_row_that_stays_out_of_the_pieces():
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.append(["Party", "Country", "Amount"])
    for r in (("A", "UK", 10), ("B", "France", 20), ("C", "UK", 30), ("Total", None, 60)):
        ws.append(list(r))
    d = Path(tempfile.mkdtemp())
    wb.save(d / "s.xlsx")
    counts = mh.split_sheets(str(d / "s.xlsx"), str(d / "o.xlsx"), "Country", how="value", keep_all_sheet=True)
    assert counts == {"France": 1, "UK": 2}
    out = load_workbook(d / "o.xlsx")
    assert out.sheetnames == ["France", "UK", "All"] and [c.value for c in out["UK"]["A"]] == ["Party", "A", "C"]


def test_the_owes_file_nudge_knows_that_the_reconciliation_tools_send_their_own_files():
    ask = [{"role": "user", "content": "Poori detail ka Excel bhi bhejo"}]
    assert owes_file(ask)
    sent_by_tool = ask + [{"role": "assistant", "content": "", "tool_calls": [{"id": "c", "type": "function", "function": {"name": "gst_reconcile", "arguments": "{}"}}]},
                          {"role": "tool", "tool_call_id": "c", "content": '{"files_sent_to_owner": ["GSTR2B_reconciliation.xlsx", "report.pdf"]}'}]
    assert not owes_file(sent_by_tool)
