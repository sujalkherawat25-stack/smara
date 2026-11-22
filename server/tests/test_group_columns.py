"""A value written once for a whole group (a room number on the first row of the room only), and a header word that only says 'money'.

Found on a real room-wise electricity sheet: 'Room 407 mein kis kis ka baaki hai?' was answered with the first tenant of the room only (the
rows below it had no room number), and 'Sep ke bill, collection aur total' was refused because the header says 'Bill Amount'."""
from pathlib import Path

from openpyxl import Workbook, load_workbook

from src.agent import sheet_engine as se

ROOMS = [(101, ["Asha", "Bina", "Chitra"]), (102, ["Divya", "Esha"]), (103, ["Farah", "Gita", "Hema"]), (104, ["Isha"]), (105, ["Jaya", "Kiran"]), (106, ["Lata", "Mona"])]


def pg_book(path: Path) -> Path:
    """Fictional PG sheet: room only on the first row of each room, a Total row, and a block below it that also writes the room once."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["Room no.", "Name", "Deposit", "Sep-26 Bill Amount", "Sep-26 Collection", "Sep-26 Total", "Remark"])
    r = 2
    for room, names in ROOMS:
        for i, nm in enumerate(names):
            bill = 100 * (room - 100) + 10 * i
            ws.append([room if i == 0 else None, nm, 500, bill, bill if nm != "Esha" else 0, f"=E{r}-D{r}", "Paid" if nm != "Esha" else "Pending"])
            r += 1
    ws.append([None, "Total", f"=SUM(C2:C{r - 1})", None, None, None, None])
    ws.append([])
    ws.append([103, "Nisha", None, 300, 300, None, None])
    ws.append([None, "Opal", None, 300, 0, None, None])
    ws.append([107, "Pia", None, 50, 50, None, None])
    wb.save(path)
    return path


def rows_of(res):
    return res["answers"][0]["matching_rows"]


def test_a_room_written_once_covers_every_row_of_the_room(tmp_path):
    p = pg_book(tmp_path / "pg.xlsx")
    res = se.query(str(p), [{"where": [{"column": "Room no.", "op": "=", "value": 103}], "list": True, "columns": ["Room no.", "Name", "Sep-26 Bill Amount"]}])
    assert [x["Name"] for x in rows_of(res)] == ["Farah", "Gita", "Hema"]
    assert all(x["Room no."] == 103 for x in rows_of(res))                      # the answer shows the room on every row, not only the first
    assert "Room no." in res["filled_down"] and "first row of each group" in res["filled_down"]["Room no."]
    one = se.query(str(p), [{"where": [{"column": "Room no.", "op": "=", "value": 104}], "measures": [{"op": "sum", "of": "Sep-26 Bill Amount"}]}])
    assert one["answers"][0]["rows_matched"] == 1
    by_room = se.query(str(p), [{"group_by": "Room no.", "measures": [{"op": "sum", "of": "Sep-26 Bill Amount"}]}])
    ans = by_room["answers"][0]
    assert ans["groups"] == 6 and ans["rows_matched"] == 13, ans                 # six rooms, not six rooms plus a group of 'blank'
    assert {r["Room no."]: r["sum of Sep-26 Bill Amount"] for r in ans["rows"]}[101] == 330     # 100 + 110 + 120: all three tenants of room 101


def test_the_block_under_the_totals_carries_its_own_rooms(tmp_path):
    p = pg_book(tmp_path / "pg.xlsx")
    res = se.query(str(p), [{"where": [{"column": "Room no.", "op": "=", "value": 103}], "list": True, "columns": ["Name"]}], include_rows_below_totals=True)
    assert [x["Name"] for x in rows_of(res)] == ["Farah", "Gita", "Hema", "Nisha", "Opal"]      # Opal has no room of her own: she is under Nisha's 103
    without = se.query(str(p), [{"where": [{"column": "Room no.", "op": "=", "value": 103}], "list": True, "columns": ["Name"]}])
    assert [x["Name"] for x in rows_of(without)] == ["Farah", "Gita", "Hema"] and "rows_below_totals" in without["not_counted"]


def test_layout_says_which_column_is_written_once_per_group(tmp_path):
    p = pg_book(tmp_path / "pg.xlsx")
    lay = se.layout(str(p))["sheets"][0]
    room = next(c for c in lay["columns"] if c["header"] == "Room no.")
    assert "written_once_per_group" in room and "same value" in room["written_once_per_group"]
    assert not any("written_once_per_group" in c for c in lay["columns"] if c["header"] != "Room no.")
    row3 = se.layout(str(p), rows="3-3")["sheets"][0]["rows"][0]                  # Bina: her room is written on Asha's row only
    assert row3["A Room no. (group)"] == 101 and row3["B Name"] == "Bina"


def test_a_column_that_only_looks_like_a_group_is_left_alone(tmp_path):
    wb = Workbook()
    ws = wb.active
    ws.append(["Ref", "Party", "Status", "Amount"])
    # Ref: the first row is empty; Status: the same word again and again; Party: filled on every row
    for i in range(10):
        ws.append([f"R{i}" if i % 3 == 1 else None, f"P{i}", "Paid" if i % 2 else None, 100 + i])
    p = tmp_path / "x.xlsx"
    wb.save(p)
    sh = se.load(str(p))["sheets"][0]
    assert sh.group_columns() == {}


def test_an_edit_on_rows_picked_by_room_reaches_every_row_of_the_room(tmp_path):
    p, out = pg_book(tmp_path / "pg.xlsx"), str(tmp_path / "o.xlsx")
    r = se.apply(str(p), out, [{"column": "Remark", "rows": {"where": [{"column": "Room no.", "op": "=", "value": 101}]}, "value": "Checked"}],
                 owner_words="Room 101 ke Remark mein Checked likh do")
    assert not r.get("error"), r
    ws = load_workbook(out).active
    assert [ws.cell(i, 7).value for i in (2, 3, 4)] == ["Checked"] * 3 and ws.cell(5, 7).value == "Paid"


def test_a_header_word_that_only_says_money_does_not_stop_an_edit():
    said = "Meri Total row mein Sep ke bhi totals daal do (bill, collection aur total)"
    assert se.named_by_owner("Sep-26 Bill Amount", "H", said)                     # the owner said 'bill'; 'Amount' is only 'money'
    assert se.named_by_owner("Sep-26 Collection", "J", said) and se.named_by_owner("Sep-26 Total", "K", said)
    assert not se.named_by_owner("Aug-26 Bill Amount", "D", said)                 # another month
    assert not se.named_by_owner("Sep-26 Paid Arpita", "I", said)                 # a column they did not name
    assert se.named_by_owner("Amount", "B", "Amount 10% kam kar do")              # a header of one word is still that word
    assert not se.named_by_owner("Amount Received", "C", "Amount 10% kam kar do")


def test_the_sep_totals_edit_goes_through_and_changes_only_the_total_row(tmp_path):
    p, out = pg_book(tmp_path / "pg.xlsx"), str(tmp_path / "o.xlsx")
    ws = load_workbook(p).active
    total_row = next(i for i in range(1, ws.max_row + 1) if ws.cell(i, 2).value == "Total")
    said = "Total row mein Sep ke totals daal do (bill, collection aur total), baaki kuch mat badalna"
    r = se.apply(str(p), out, [{"column": "Sep-26 Bill Amount", "rows": [total_row], "formula": f"=SUM(D2:D{total_row - 1})"},
                               {"column": "Sep-26 Total", "rows": [total_row], "formula": f"=SUM(F2:F{total_row - 1})"}], owner_words=said)
    assert not r.get("error"), r
    assert se.outside_what_was_asked(str(p), out, said) == []


def dup_book(path: Path) -> Path:
    """Three people called Mahi: two in room 101 (one has paid 80, the other nothing) and one in room 103."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["Room no.", "Name", "Deposit", "Sep-26 Bill Amount", "Sep-26 Collection", "Sep-26 Total", "Remark"])
    data = [(101, "Mahi", 100, 80), (None, "Mahi", 100, None), (102, "Neha", 70, 70), (None, "Opal", 70, None), (103, "Mahi", 60, None), (None, "Pia", 60, 60),
            (104, "Quin", 90, 90), (105, "Rani", 55, 55), (None, "Sara", 55, None)]
    for i, (room, nm, bill, got) in enumerate(data, start=2):
        ws.append([room, nm, 500, bill, got, f"=E{i}-D{i}", None])
    wb.save(path)
    return path


def edit(path, said, row, extra=None, tmp=None):
    out = str(Path(tmp or path.parent) / "o.xlsx")
    return se.apply(str(path), out, [{"column": "Sep-26 Collection", "rows": [row], "value": 90, **(extra or {})}], owner_words=said)


def test_a_person_on_three_rows_is_asked_about_not_guessed(tmp_path):
    p = dup_book(tmp_path / "d.xlsx")
    r = edit(p, "Mahi ka Sep Collection 90 kar do", 3)
    assert r["nothing_changed"] and "'Mahi' is on 3 rows" in r["error"], r
    assert "row 2, Room no. 101, now 80" in r["error"] and "row 3, Room no. 101, now empty" in r["error"] and "row 6, Room no. 103, now empty" in r["error"]
    assert "ask_owner" in r["error"] and "owner_confirmed_row" in r["error"]
    ok = edit(p, "Mahi ka Sep Collection 90 kar do", 3, {"owner_confirmed_row": True})       # after the owner picked one of the options
    assert not ok.get("error") and ok.get("verified"), ok


def test_a_room_a_row_or_a_figure_only_one_row_has_says_which_person(tmp_path):
    p = dup_book(tmp_path / "d.xlsx")
    assert not edit(p, "Room 103 ki Mahi ka Sep Collection 90 kar do", 6).get("error")             # only one Mahi in 103
    assert not edit(p, "Mahi (row 6) ka Sep Collection 90 kar do", 6).get("error")
    assert not edit(p, "Room 101 ki Mahi, jiska Sep Collection 80 hai, ka 90 kar do", 2).get("error")      # 80 is held by that row only
    two_in_one_room = edit(p, "Room 101 ki Mahi ka Sep Collection 90 kar do", 3)
    assert two_in_one_room.get("error") and "'Mahi' is on 3 rows" in two_in_one_room["error"]       # both Mahis are in 101: still not clear
    assert not edit(p, "Neha ka Sep Collection 90 kar do", 4).get("error")                          # a name on one row only
    assert not edit(p, "Sep Collection 90 kar do", 3).get("error")                                  # no name in the words: nothing to ask about
