"""Changing a workbook the owner sent must keep every sheet, row, formula and format. Only the new column may be added."""
import sys
from datetime import datetime
from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sandbox"))
import smara_helpers as mh  # noqa: E402


@pytest.fixture
def original(tmp_path):
    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["Slno", "Agency", "TotalPremium", "Req lives"])          # header on row 1
    for i in range(1, 6):
        ws.append([i, f"Agent {i}", 1000 * i, f"=(6-A{i + 1})"])
    ws["C1"].font = Font(bold=True)
    ws2 = wb.create_sheet("UPDATED")
    for _ in range(3):                                                   # header on row 4, blank rows above, like the real file
        ws2.append([None])
    ws2.append(["Slno", "Agency", "Total Premium", "Appnt.Date"])
    ws2.append([1, "Ram", 500, datetime(2004, 3, 8)])
    ws2.append([2, "Lal", 700, "31/08/1996"])
    ws3 = wb.create_sheet("NOTES")
    ws3.append(["Just", "some", "notes"])
    path = tmp_path / "orig.xlsx"
    wb.save(path)
    return path


def test_add_column_changes_nothing_else_and_reports_the_sheet_it_could_not_use(original, tmp_path):
    out = tmp_path / "new.xlsx"
    report = mh.add_column(str(original), str(out), source_header="TotalPremium", new_header="Bonus 40%", formula="={c}{r}*0.4")
    by = {r["sheet"]: r for r in report}
    assert by["Sheet1"]["rows_filled"] == 5 and by["Sheet1"]["column"] == "E" and by["Sheet1"]["header_row"] == 1
    assert by["UPDATED"]["rows_filled"] == 2 and by["UPDATED"]["header_row"] == 4         # "Total Premium" matched, row 4
    assert "skipped" in by["NOTES"]
    wb = load_workbook(out)
    assert wb.sheetnames == ["Sheet1", "UPDATED", "NOTES"]
    assert wb["Sheet1"]["E1"].value == "Bonus 40%" and wb["Sheet1"]["E2"].value == "=C2*0.4"
    assert wb["Sheet1"]["D2"].value == "=(6-A2)"                                          # the owner's formula is intact
    assert wb["Sheet1"]["C1"].font.bold and wb["Sheet1"]["E1"].font.bold is not None
    assert wb["UPDATED"]["D5"].value == datetime(2004, 3, 8) and wb["UPDATED"]["E6"].value == "=C6*0.4"
    cmp = mh.compare_workbooks(str(original), str(out))
    assert cmp["ok"] and cmp["sheets"][0]["cells_changed"] == 0 and cmp["sheets"][0]["cells_added"] == 6


def test_compare_catches_a_rebuilt_file_that_lost_rows_or_changed_cells(original, tmp_path):
    bad = tmp_path / "bad.xlsx"
    wb = load_workbook(original)
    wb["Sheet1"].delete_rows(5)                       # a lost row
    wb["Sheet1"]["C2"] = 999                          # a changed value
    del wb["NOTES"]                                   # a lost sheet
    wb.save(bad)
    cmp = mh.compare_workbooks(str(original), str(bad))
    assert not cmp["ok"]
    names = {s["sheet"]: s for s in cmp["sheets"]}
    assert names["NOTES"]["missing_sheet"] and names["Sheet1"]["cells_changed"] >= 1 and not names["Sheet1"]["same_apart_from_additions"]


def test_add_column_refuses_when_no_sheet_has_the_header(original, tmp_path):
    with pytest.raises(ValueError):
        mh.add_column(str(original), str(tmp_path / "x.xlsx"), source_header="Nope", new_header="X", formula="={c}{r}")
