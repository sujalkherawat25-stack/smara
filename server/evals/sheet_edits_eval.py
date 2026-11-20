"""Spreadsheet work with the real model: does Smara do exactly what was said, ask when it is unclear, and keep earlier edits?

    python -m evals.sheet_edits_eval --db postgresql://.../smara_eval --model glm5.3-flash --sandbox container --jobs /jobs \
        [--register /path/real_register.xlsx] [--big "/path/Online Retail.xlsx"] [--only a,b] [--json out.json]

Built from the 20 Nov 2026 sessions (a hostel's electricity register). Every check reads the file Smara sent back (cell by
cell against the original) or what it asked; never only its wording. Without --register a made-up register with the same
shape is used (header, entries, a Total row with SUMs, a separate block under the totals, two students with the same name).
"""
import argparse
import asyncio
import io
import re
import selectors
import sys

from openpyxl import Workbook, load_workbook

from evals import agent_harness as h
from evals import demo_scenarios as ds      # importing it routes owner file downloads to the in-memory files
from evals.agent_harness import OWNER, Task, has_num, owner
from src.agent import sheet_engine as se
from src.ingest.dispatch import dispatch

REG, BIG = "MXR", "MXB"
h.FILES.setdefault(REG, ("Electricity Bill Details Aug Sep 2026.xlsx", b""))     # the bytes are filled in by setup()
h.FILES.setdefault(BIG, ("Online Retail.xlsx", b""))


def made_up_register() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.append(["Room no.", "Name", "Deposit till 31st july", "Aug-26 Bill Amount", "Aug-26 Paid Arpita", "Aug Collection",
               "Aug Total", "Sep-26 Bill Amount", "Sep-26 Paid Arpita", "Sep-26 Collection", "Sep-26 Total"])
    people = [(301, "Asha", 1064, 0, 0, None, 420, 420, 420), (None, "Bela", 4663, 732, 732, None, 420, 420, None),
              (302, "Chhavi", 0, 1000, 0, 1000, 1428, None, 1428), (None, "Nidhi", 0, 1404, 0, None, 1578, None, None),
              (None, "Esha", 0, 1404, 0, None, 1578, None, None), (305, "Stuti", 0, 638, 0, 638, 648, None, 648),
              (306, "Nidhi", 2791, 347, 347, None, 676, 376, None), (None, "Gauri", 0, 362, 313, None, 885, None, 855),
              (307, "Hetal", 0, 665, 258, 665, 636, None, 636), (None, "Ishita", 0, 750, 0, None, 1416, None, 1416)]
    for room, name, dep, ab, ap, ac, sb, sp, sc in people:
        r = ws.max_row + 1
        ws.append([room, name, dep, ab, ap, ac, f"=E{r}+F{r}-D{r}", sb, sp, sc, f"=I{r}+J{r}-H{r}"])
    last = ws.max_row
    ws.append([None, "Total", f"=SUM(C2:C{last})", f"=SUM(D2:D{last})", f"=SUM(E2:E{last})", f"=SUM(F2:F{last})",
               f"=SUM(G2:G{last})", None, f"=SUM(I2:I{last})"])
    ws.append([])
    ws.append([307, "Happy Patel", None, None, None, None, None, 352, None, 352])
    ws.append([411, "Bhoomi", None, 323, None, 323, None, 724])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


ORIG: dict = {}


def setup(register: bytes, big: bytes | None):
    h.FILES[REG] = ("Electricity Bill Details Aug Sep 2026.xlsx", register)
    if big:
        h.FILES[BIG] = ("Online Retail.xlsx", big)
    import tempfile
    path = tempfile.mktemp(suffix=".xlsx")
    open(path, "wb").write(register)
    book = se.load(path)
    ORIG["sheet"] = book["sheets"][0]
    ORIG["cells"] = dict(book["sheets"][0].cells)


# ---------------------------------------------------------------- reading what came back
def sent_xlsx(env):
    xl = [u for u in env.wa.uploads if u[0].lower().endswith((".xlsx", ".xlsm"))]
    if not xl:
        return None
    wb = load_workbook(io.BytesIO(xl[-1][2]))
    ws = wb.active
    return {(c.row, c.column): c.value for row in ws.iter_rows() for c in row if c.value is not None}


def diff(after: dict) -> dict:
    before = ORIG["cells"]
    return {k: (before.get(k), after.get(k)) for k in set(before) | set(after) if before.get(k) != after.get(k)}


def col(name: str) -> int:
    c, err = ORIG["sheet"].find_column(name)
    assert not err, err
    return c


def entries() -> list[int]:
    sh = ORIG["sheet"]
    return [r for r in sh.data_rows() if sh.has_content(r)]


def asked(env) -> bool:
    t = env.wa.last_owner_text()
    return bool(re.search(r"(^|\n)\s*1[.)]", t) and re.search(r"(^|\n)\s*2[.)]", t)) or t.strip().endswith("?")


def texts(env) -> str:
    return "\n".join(m["text"]["body"] for m in env.wa.to(OWNER) if m.get("type") == "text")


def only_cols(d: dict, cols: set[int]) -> tuple[bool, str]:
    stray = sorted(k for k in d if k[1] not in cols)
    sh = ORIG["sheet"]
    protected = [k for k in d if k[0] in sh.total_rows or k[0] in sh.below]
    bad = stray + protected
    return not bad, f"changed outside the asked column/rows: {bad[:8]}" if bad else ""


# ---------------------------------------------------------------- checks
async def c_write_paid(env):
    after = sent_xlsx(env)
    if after is None:
        return False, f"no file sent; said: {env.wa.last_owner_text()[:200]!r}"
    f = col("Aug Collection")
    d = diff(after)
    ok, why = only_cols(d, {f})
    wrote = [r for r in entries() if str(after.get((r, f)) or "").strip().lower() == "paid"]
    return ok and len(wrote) == len(entries()), why or f"'paid' in {len(wrote)}/{len(entries())} entry rows"


async def c_unclear_asks(env):
    if sent_xlsx(env) is not None:
        return False, "sent a file without asking what 'paid kardo' means"
    return asked(env), f"did not ask; said: {env.wa.last_owner_text()[:200]!r}"


async def ev_unclear_then_answer(env):
    await dispatch(env.app, owner("Iss excel file mein aug collection ko paid kardo pura aur bss isi ko edit karke mujhe file dedo",
                                  "u1", REG))
    env.asked_first = asked(env) and sent_xlsx(env) is None
    if not env.asked_first:
        return
    await dispatch(env.app, owner("Jinka Aug ka paisa baaki hai, unka baaki amount Aug Collection mein daal do, taaki Aug Total 0 ho jaye", "u2"))


async def c_unclear_then_answer(env):
    if not getattr(env, "asked_first", False):
        return False, f"did not ask first; said: {env.wa.last_owner_text()[:200]!r}"
    after = sent_xlsx(env)
    if after is None:
        return False, f"no file after the answer; said: {env.wa.last_owner_text()[:200]!r}"
    f, g = col("Aug Collection"), col("Aug Total")
    ok, why = only_cols(diff(after), {f})
    ev = se._Eval(after, {})
    owing = [r for r in entries() if se.as_number(ev.value(r, g)) not in (se.ERR,) and ev.value(r, g) < 0]
    return ok and not owing, why or f"rows still owing in Aug Total: {owing[:6]}"


async def ev_followup(env):
    await dispatch(env.app, owner("Sep-26 Collection mein jinka Sep ka baaki hai, unka baaki amount daal do taaki Sep-26 Total 0 ho jaye",
                                  "f1", REG))
    await dispatch(env.app, owner("ab Aug ke liye bhi same karo, Aug Collection mein", "f2"))


async def c_followup(env):
    after = sent_xlsx(env)
    if after is None:
        return False, f"no file; said: {env.wa.last_owner_text()[:200]!r}"
    if len([u for u in env.wa.uploads if u[0].lower().endswith(".xlsx")]) < 2:
        return False, "only one file came back for two requests"
    f, j = col("Aug Collection"), col("Sep-26 Collection")
    ok, why = only_cols(diff(after), {f, j})
    ev = se._Eval(after, {})
    g, k = col("Aug Total"), col("Sep-26 Total")
    left = [(r, c) for r in entries() for c in (g, k) if isinstance(ev.value(r, c), (int, float)) and ev.value(r, c) < 0]
    return ok and not left, why or f"the last file still has dues (the first change was lost?): {left[:6]}"


async def c_named_people(env):
    after = sent_xlsx(env)
    if after is None:     # two people are called Nidhi: asking which one is right
        return asked(env) and "nidhi" in env.wa.last_owner_text().lower(), f"said: {env.wa.last_owner_text()[:200]!r}"
    f = col("Aug Collection")
    d = diff(after)
    ok, why = only_cols(d, {f})
    rows = {k[0] for k in d}
    names = {str(ORIG["cells"].get((r, col("Name"))) or "").strip().lower() for r in rows}
    return ok and names <= {"stuti", "nidhi"} and "stuti" in names, why or f"changed rows of: {names}"


async def c_summary(env):
    sh = ORIG["sheet"]
    d = col("Aug-26 Bill Amount")
    bill = sum(v for r in sh.data_rows() if isinstance(v := sh.ev.value(r, d), (int, float)))
    g = col("Aug Total")
    owing = sum(1 for r in entries() if isinstance(v := sh.ev.value(r, g), (int, float)) and v < 0)
    t = texts(env)
    ok = has_num(t, f"{int(bill):,}") and re.search(rf"\b{owing}\b", t) is not None
    return ok, f"expected bill {int(bill):,} and {owing} students; said: {t[-300:]!r}"


async def c_status_column(env):
    after = sent_xlsx(env)
    if after is None:
        return False, f"no file; said: {env.wa.last_owner_text()[:200]!r}"
    sh = ORIG["sheet"]
    new = [c for (r, c), v in after.items() if r == sh.header_row and isinstance(v, str) and "status" in v.lower()]
    if not new:
        return False, "no 'Aug Status' column"
    nc = new[0]
    d = diff(after)
    ok, why = only_cols(d, {nc})
    g = col("Aug Total")
    wrong = []
    for r in entries():
        v = sh.ev.value(r, g)
        want = "clear" if isinstance(v, (int, float)) and v >= 0 else "pending"
        if str(after.get((r, nc)) or "").strip().lower() != want:
            wrong.append(r)
    return ok and not wrong, why or f"wrong status in rows {wrong[:8]}"


async def c_big(env):
    t = texts(env)
    return has_num(t, "541,909") and (has_num(t, "5,176,450") or has_num(t, "51,76,450")) and "united kingdom" in t.lower(), t[-400:]


TASKS = [
    Task("write_paid_exactly", ("owner", owner("Is excel mein Aug Collection column mein har student ke aage 'paid' likh do", "w1", REG)),
         c_write_paid),
    Task("unclear_asks_first", ("owner", owner("Iss excel file mein aug collection ko paid kardo pura aur bss isi ko edit karke mujhe file dedo",
                                               "a1", REG)), c_unclear_asks),
    Task("unclear_then_answer", ("custom", ev_unclear_then_answer), c_unclear_then_answer),
    Task("followup_keeps_first_edit", ("custom", ev_followup), c_followup),
    Task("named_people_only", ("owner", owner("Stuti aur Nidhi ke Aug Collection mein 'paid' likh do", "n1", REG)), c_named_people),
    Task("summary_from_the_sheet", ("owner", owner("Is file mein Aug ka total bill kitna hai, aur kitne students ka Aug abhi baaki hai?",
                                                   "s1", REG)), c_summary),
    Task("status_column", ("owner", owner("Ek naya column 'Aug Status' add karo: jinka Aug Total 0 ya usse zyada hai unke liye Clear, "
                                          "baaki sab ke liye Pending", "c1", REG)), c_status_column),
    Task("big_file_question", ("owner", owner("Is file mein kitni rows hain, total Quantity kitni hai, aur sabse zyada orders kis country "
                                              "ke hain?", "b1", BIG)), c_big),
]


if __name__ == "__main__":
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--register", default="")
    pre.add_argument("--big", default="")
    known, rest = pre.parse_known_args()
    reg = open(known.register, "rb").read() if known.register else made_up_register()
    big = open(known.big, "rb").read() if known.big else None
    setup(reg, big)
    sys.argv = [sys.argv[0]] + rest
    h.TASKS[:] = [t for t in TASKS if big or t.name != "big_file_question"]
    h.seed = ds.seed
    h.run_task = ds.run_task
    asyncio.run(h.main(), loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))
