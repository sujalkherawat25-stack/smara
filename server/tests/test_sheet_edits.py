"""Spreadsheet edits do exactly what was asked (the 20 Nov 2026 hostel electricity register, with made-up names).

The real failures: asked to write 'paid' in one column, the agent zeroed another, overwrote the Total row's SUM, wrote into
the block below the totals, started a follow-up edit from the original (losing the first change) and reported 'nothing
else changed' when its own compare said otherwise.
"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from openpyxl import Workbook, load_workbook

from src.agent import sheet_engine as se
from src.agent import sheet_tools
from src.agent.policy import SessionCtx, pre_tool_use
from src.agent.tools import TOOL_NAMES
from src.agent_loop import EXECUTORS, asked_owner
from src.box.sandbox import Sandbox
from src.loop import owner_channel

HEAD = ["Room no.", "Name", "Deposit till 31st july", "Aug-26 Bill Amount", "Aug-26 Paid Arpita", "Aug Collection",
        "Aug Total", "Sep-26 Bill Amount", "Sep-26 Paid Arpita", "Sep-26 Collection", "Sep-26 Total"]
# room, name, deposit, aug bill, aug paid, aug coll, sep bill, sep paid, sep coll
PEOPLE = [(301, "Asha", 1064, 0, 0, None, 420, 420, 420),
          (None, "Bela", 4663, 732, 732, None, 420, 420, None),
          (302, "Chhavi", 0, 1000, 0, 1000, 1428, None, 1428),
          (None, "Diya", 0, 1404, 0, None, 1578, None, None),        # owes both months
          (None, "Esha", 0, 1404, 0, None, 1578, None, None),        # owes both months
          (303, "Fatima", 0, 638, 0, 638, 648, None, 648),
          (None, "Gauri", 2791, 347, 347, None, 676, 376, None),     # owes 300 for Sep
          (None, None, None, None, 0, None, None, None, None)]       # an empty template line, only formulas


def register(path: Path) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(HEAD)
    for room, name, dep, ab, ap, ac, sb, sp, sc in PEOPLE:
        r = ws.max_row + 1
        ws.append([room, name, dep, ab, ap, ac, f"=E{r}+F{r}-D{r}", sb, sp, sc, f"=I{r}+J{r}-H{r}"])
    last = ws.max_row
    t = last + 1
    ws.append([None, "Total", f"=SUM(C2:C{last})", f"=SUM(D2:D{last})", f"=SUM(E2:E{last})", f"=SUM(F2:F{last})",
               f"=SUM(G2:G{last})", None, f"=SUM(I2:I{last})"])
    ws.append([])
    ws.append([307, "Hina (new)", None, None, None, None, None, 352, None, 352])      # the block under the totals
    ws.append([411, "Ira (new)", None, 323, None, 323, None, 724])
    ws["A1"].font = ws["A1"].font.copy(bold=True)
    wb.save(path)
    return path


@pytest.fixture
def reg(tmp_path):
    return register(tmp_path / "Electricity Aug Sep.xlsx")


def cells(path):
    return {(c.row, c.column): c.value for row in load_workbook(path).active.iter_rows() for c in row if c.value is not None}


def test_layout_knows_the_header_the_entries_the_total_row_and_the_block_below(reg):
    s = se.layout(str(reg))["sheets"][0]
    assert s["header_row"] == 1 and s["data_rows"] == "2-9" and s["total_rows"][0]["row"] == 10
    assert s["rows_below_totals"]["rows"] == "12-13" and s["rows_below_totals"]["count"] == 2
    cols = {c["header"]: c for c in s["columns"]}
    assert cols["Aug-26 Bill Amount"]["sum_of_data_rows"] == 5525          # not doubled by the Total row or the block below
    assert cols["Aug Total"]["sum_of_data_rows"] == -2808                   # worked out, the file has no cached values
    assert any("Aug Total" in u for u in cols["Aug Collection"]["used_by_formulas_in"])
    found = se.layout(str(reg), find="diya")["sheets"][0]["found"]
    assert len(found) == 1 and found[0]["B Name"] == "Diya" and found[0]["G Aug Total"].endswith("-1404")


def test_writing_paid_changes_that_column_only_and_says_what_it_breaks(reg, tmp_path):
    out = tmp_path / "out.xlsx"
    before = cells(reg)
    r = se.apply(str(reg), str(out), [{"column": "aug collection", "value": "Paid"}])
    assert r["saved"] and r["verified"]
    ch = r["changes"][0]
    assert ch["column"] == "F 'Aug Collection'" and ch["rows_changed"] == 7      # 7 entries; not the empty line
    assert ch["total_rows_left_alone"] == [10] and ch["rows_below_totals_left_alone"]["with_a_value_in_this_column"] == 1
    assert any("#VALUE!" in e and "Aug Total" in e for e in r["effects"])
    after = cells(out)
    changed = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
    assert changed == {(r_, 6) for r_ in range(2, 9)}                          # F2:F8, nothing else at all
    assert after[(10, 6)] == "=SUM(F2:F9)" and after[(13, 6)] == 323             # the Total and the block below stay
    assert after[(4, 5)] == 0                                                   # Paid Arpita untouched (it was zeroed on 20 Nov)


def test_a_followup_edit_builds_on_the_last_file_and_keeps_the_first_change(reg, tmp_path):
    a, b = tmp_path / "a.xlsx", tmp_path / "b.xlsx"
    r1 = se.apply(str(reg), str(a), [{"column": "Aug Collection", "rows": {"where": [{"column": "Aug Total", "op": "<", "value": 0}]},
                                      "compute": "{Aug-26 Bill Amount}-{Aug-26 Paid Arpita}"}])
    assert r1["verified"] and r1["changes"][0]["rows_changed"] == 2
    # the copy saved by openpyxl has no cached values: the Sep Total formulas must still be worked out to pick the rows
    r2 = se.apply(str(a), str(b), [{"column": "Sep-26 Collection", "rows": {"where": [{"column": "Sep-26 Total", "op": "<", "value": 0}]},
                                    "compute": "{Sep-26 Bill Amount}-{Sep-26 Paid Arpita}"}])
    assert r2["verified"] and r2["changes"][0]["rows_changed"] == 3
    s = se.layout(str(b))["sheets"][0]
    cols = {c["header"]: c for c in s["columns"]}
    assert cols["Aug Total"]["sum_of_data_rows"] == 0 and cols["Sep-26 Total"]["sum_of_data_rows"] == 420   # Asha paid 420 extra
    assert cells(b)[(5, 6)] == 1404                                             # the first change is still there


def test_unclear_or_wrong_columns_are_refused_with_the_choices(reg, tmp_path):
    r = se.apply(str(reg), str(tmp_path / "x.xlsx"), [{"column": "Paid Arpita", "value": 0}])
    assert r["nothing_changed"] and "E 'Aug-26 Paid Arpita'" in r["error"] and "I 'Sep-26 Paid Arpita'" in r["error"]
    r = se.apply(str(reg), str(tmp_path / "x.xlsx"), [{"column": "Oct Collection", "value": 0}])
    assert "no column called" in r["error"] and "Aug Collection" in r["error"]
    assert not (tmp_path / "x.xlsx").exists()


def test_names_rows_blanks_new_columns_and_preview(reg, tmp_path):
    out = tmp_path / "o.xlsx"
    r = se.apply(str(reg), str(out), [{"column": "Aug Collection", "rows": {"names": ["diya", "Keyur"]}, "value": "paid"}], preview=True)
    assert r["preview"] and r["changes"][0]["names_not_found"] == ["Keyur"] and r["changes"][0]["rows_changed"] == 1
    assert not out.exists()
    r = se.apply(str(reg), str(out), [{"column": "Sep-26 Collection", "only_empty": True, "value": 0},
                                      {"action": "add_column", "header": "Remarks", "rows": [3, 4], "value": "checked"},
                                      {"action": "clear", "column": "Deposit till 31st july", "rows": "2-3"}])
    assert r["verified"], r
    after = cells(out)
    assert after[(1, 12)] == "Remarks" and after[(3, 12)] == "checked" and (2, 12) not in after
    assert (2, 3) not in after and after[(4, 3)] == 0 and after[(3, 10)] == 0 and after[(2, 10)] == 420
    r = se.apply(str(reg), str(out), [{"column": "Name", "rows": [1], "value": "x"}])
    assert "header" in r["error"]


def test_a_csv_is_edited_as_a_csv(tmp_path):
    p = tmp_path / "list.csv"
    p.write_text("Name,Amount,Status\nAsha,100,\nBela,250,due\nTotal,350,\n", encoding="utf-8")
    r = se.apply(str(p), str(tmp_path / "out.csv"), [{"column": "status", "value": "paid"}])
    assert r["verified"] and r["changes"][0]["rows_changed"] == 2 and r["changes"][0]["total_rows_left_alone"] == [4]
    assert (tmp_path / "out.csv").read_text(encoding="utf-8-sig").splitlines() == [
        "Name,Amount,Status", "Asha,100,paid", "Bela,250,paid", "Total,350,"]


def test_a_file_with_a_chart_is_not_saved_without_the_owners_ok(reg, tmp_path):
    from openpyxl.chart import BarChart, Reference
    wb = load_workbook(reg)
    ch = BarChart()
    ch.add_data(Reference(wb.active, min_col=4, min_row=1, max_row=9), titles_from_data=True)
    wb.active.add_chart(ch, "M2")
    wb.save(reg)
    r = se.apply(str(reg), str(tmp_path / "o.xlsx"), [{"column": "Aug Collection", "value": "Paid"}])
    assert not r["saved"] and "charts" in r["error"]
    r = se.apply(str(reg), str(tmp_path / "o.xlsx"), [{"column": "Aug Collection", "value": "Paid", "accept_loss": True}])
    assert r["saved"] and r["verified"]


def test_formula_evaluator_handles_registers_and_never_guesses():
    ev = se._Eval({(1, 1): 5, (1, 2): "1,250", (1, 3): "=A1+B1-2", (2, 3): "=SUM(C1:C1)*2", (3, 3): "=VLOOKUP(A1,X:Y,2)",
                   (4, 3): "=A1+D9", (5, 3): '=A1+"x"', (6, 3): "=ROUND(A1/3,1)"})
    assert ev.value(1, 3) == 1253 and ev.value(2, 3) == 2506 and ev.value(3, 3) is se.UNK
    assert ev.value(4, 3) == 5 and ev.value(5, 3) == se.ERR and ev.value(6, 3) == 1.7


# ---------------------------------------------------------------- the tools around the engine
class Box(Sandbox):
    pass


@pytest.fixture
def app(tmp_path, monkeypatch, reg):
    sb = Sandbox(str(tmp_path / "jobs"), mode="local")
    data = reg.read_bytes()

    async def fetch(app, ref, ctx=None):
        if ref.startswith("sandbox:"):
            _, jid, name = ref.split(":", 2)
            return sb.read_output(jid, name), "", name
        return data, "", "Electricity Aug Sep.xlsx"
    monkeypatch.setattr(sheet_tools.actions, "_fetch", fetch)
    return SimpleNamespace(state=SimpleNamespace(sandbox=sb))


async def test_edit_tool_returns_a_sendable_file_under_the_owners_name_and_chains(app):
    ctx = SimpleNamespace(tenant_id="t")
    look = await sheet_tools.open_spreadsheet(app, ctx, {"file_ref": "local:abc:Electricity Aug Sep.xlsx"})
    assert look["sheets"][0]["total_rows"][0]["row"] == 10 and "how_to_read" in look
    r = await sheet_tools.edit_spreadsheet(app, ctx, {"file_ref": "local:abc:x.xlsx",
                                                      "changes": [{"column": "Aug Collection", "value": "Paid"}]})
    assert r["verified"] and r["filename"] == "Electricity_Aug_Sep.xlsx" and r["file_ref"].startswith("sandbox:")
    assert r["_new_files"] == [r["file_ref"]] and "THIS file_ref" in r["next"]
    r2 = await sheet_tools.edit_spreadsheet(app, ctx, {"file_ref": r["file_ref"],
                                                       "changes": {"column": "Sep-26 Collection", "only_empty": True, "value": 0}})
    assert r2["verified"]
    _, jid, name = r2["file_ref"].split(":", 2)
    out = load_workbook(__import__("io").BytesIO(app.state.sandbox.read_output(jid, name))).active
    assert out["F2"].value == "Paid" and out["J3"].value == 0                     # both changes are in the last file
    bad = await sheet_tools.edit_spreadsheet(app, ctx, {"file_ref": "local:abc:x.xlsx", "changes": "write paid"})
    assert "changes must be a list" in bad["error"]


def test_tools_are_registered_and_guarded():
    for name in ("open_spreadsheet", "edit_spreadsheet", "ask_owner"):
        assert name in TOOL_NAMES and name in EXECUTORS
    owner = SessionCtx(id="s", tenant_id="t", trust="TRUSTED", files=["local:a:x.xlsx"])
    other = SessionCtx(id="s", tenant_id="t", trust="UNTRUSTED", files=[])
    assert pre_tool_use(owner, "edit_spreadsheet", {"file_ref": "local:a:x.xlsx", "changes": []}, TOOL_NAMES).verdict == "allow"
    assert pre_tool_use(other, "edit_spreadsheet", {"file_ref": "local:a:x.xlsx", "changes": []}, TOOL_NAMES).verdict == "deny"
    assert pre_tool_use(other, "ask_owner", {"question": "Which one?"}, TOOL_NAMES).verdict == "deny"


def test_a_question_to_the_owner_ends_the_turn():
    calls = [{"id": "c1", "function": {"name": "ask_owner", "arguments": "{}"}}]
    msgs = [{"role": "assistant", "content": "", "tool_calls": calls},
            {"role": "tool", "tool_call_id": "c1", "content": json.dumps({"asked": True})}]
    assert asked_owner(msgs, calls)
    assert not asked_owner(msgs[:1] + [{"role": "tool", "tool_call_id": "c1", "content": '{"error": "x"}'}], calls)


class FakeTG:
    def __init__(self):
        self.sent = []

    async def send(self, payload):
        self.sent.append(payload)


async def test_options_become_telegram_buttons_and_never_reach_whatsapp(monkeypatch):
    from src.config import Settings
    from src.loop.whatsapp import text_message
    tg = FakeTG()
    app = SimpleNamespace(state=SimpleNamespace(pool=None, settings=Settings(), tg=tg, wa=None))
    tenant = SimpleNamespace(id="t", owner_phone="919999999999", telegram_chat_id=42, owner_last_channel="telegram",
                             owner_last_inbound_at=None)
    payload = {**text_message("919999999999", "Kaunsa?\n\n1. Aug\n2. Sep"), "_options": ["1. Aug", "2. Sep"]}
    assert await owner_channel._deliver(app, tenant, payload, kind="owner_text", reply_channel="telegram") == "telegram"
    kb = tg.sent[0]["reply_markup"]
    assert kb["keyboard"] == [[{"text": "1. Aug"}], [{"text": "2. Sep"}]] and kb["one_time_keyboard"]
    assert "_options" not in owner_channel._clean(payload)


def test_book_rules_only_reach_people_who_keep_books_or_ask_about_money():
    from src.agent.prompt import BASE, BOOKS, keeps_books
    none = "No ledger is set up for this user"
    assert not keeps_books(none, "TRUSTED", "Best break habits between lectures?")
    assert keeps_books(none, "TRUSTED", "ye mera outstanding hai")
    assert keeps_books(none, "TRUSTED", "[The owner attached a file: file_ref=local:a:x.xlsx]")
    assert keeps_books("Ledger right now: 3 open invoices", "TRUSTED", "hi")
    assert keeps_books(none, "SYSTEM", "") and keeps_books(none, "UNTRUSTED", "")
    assert "record_sale" in BOOKS and "record_payment" not in BASE and "real tools" in BASE


def test_a_name_on_two_rows_is_not_guessed(tmp_path):
    p = tmp_path / "r.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.append(["Room", "Name", "Paid"])
    ws.append([304, "Nidhi ", None])
    ws.append([306, "Nidhi", None])
    ws.append([305, "Stuti", None])
    wb.save(p)
    r = se.apply(str(p), str(tmp_path / "o.xlsx"), [{"column": "Paid", "rows": {"names": ["Nidhi"]}, "value": "paid"}])
    assert "Nidhi on rows 2, 3" in r["error"] and r["nothing_changed"]
    r = se.apply(str(p), str(tmp_path / "o.xlsx"), [{"column": "Paid", "rows": {"names": ["Nidhi"], "all_matches": True}, "value": "paid"}])
    assert r["verified"] and r["changes"][0]["rows_changed"] == 2


def test_only_columns_the_owner_named_can_change(reg, tmp_path):
    said = "Iss excel file mein aug collection ko paid kardo pura"
    assert se.named_by_owner("Aug Collection", "F", said)
    assert not se.named_by_owner("Aug-26 Paid Arpita", "E", said)          # what the agent changed on 20 Nov
    assert not se.named_by_owner("Sep-26 Collection", "J", said)           # another month
    assert se.named_by_owner("Sep-26 Collection", "J", "collection mein daal do")
    assert se.named_by_owner("Aug-26 Paid Arpita", "E", "column E mein 0 kar do")
    out = str(tmp_path / "o.xlsx")
    r = se.apply(str(reg), out, [{"column": "Aug-26 Paid Arpita", "copy_from": "Aug-26 Bill Amount"}], owner_words=said)
    assert "did not name the column E" in r["error"] and r["nothing_changed"]
    r = se.apply(str(reg), out, [{"action": "clear", "column": "Aug Collection"}], owner_words=said)
    assert "did not ask to empty" in r["error"]
    r = se.apply(str(reg), out, [{"action": "add_column", "header": "Aug Status", "value": "x"},
                                 {"column": "Aug Status", "rows": [2], "value": "y"}], owner_words="ek column Status add karo")
    assert r.get("verified"), r


def test_a_plain_formula_is_filled_down_like_excel(reg, tmp_path):
    out = tmp_path / "o.xlsx"
    r = se.apply(str(reg), str(out), [{"action": "add_column", "header": "Aug Status",
                                       "formula": '=IF(G2>=0,"Clear","Pending")'}])
    assert r["verified"], r
    after = cells(out)
    assert after[(2, 12)] == '=IF(G2>=0,"Clear","Pending")' and after[(5, 12)] == '=IF(G5>=0,"Clear","Pending")'
    shown = se._Eval(after, {})
    assert shown.value(2, 12) == "Clear" and shown.value(5, 12) == "Pending"
