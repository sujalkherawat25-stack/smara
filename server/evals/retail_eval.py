"""Real data, real model: the whole 541,909-row UCI "Online Retail" file (Kaggle's most used retail set), asked about and
edited the way an owner would on Telegram, checked against numbers worked out independently with pandas.

    python -m evals.retail_eval --db postgresql://.../smara_eval --model glm5.3 --sandbox container --jobs /jobs \
        --data "/src/Online Retail.xlsx" --truth /src/truth.json [--only a,b] [--json out.json]

Owner files go straight to the agent here (the 20 MB Telegram limit is not in play), so the full file is used.
Every check reads a number out of the reply or a cell out of the file that came back; never only the wording.
"""
import argparse
import asyncio
import io
import json
import re
import selectors
import sys

from openpyxl import load_workbook

from evals import agent_harness as h
from evals import demo_scenarios as ds
from evals.agent_harness import OWNER, Task, owner
from evals.sheet_edits_eval import asked, run_task, say_then_pick_first, texts
from src.ingest import owner_files
from src.ingest.dispatch import dispatch

owner_files.MAX_BYTES = 60 * 1024 * 1024      # the eval only: Telegram will not deliver more than 20 MB, the agent can read more

BIG = "MXB"
h.FILES.setdefault(BIG, ("Online Retail.xlsx", b""))
TRUTH: dict = {}
ORIG: dict = {}


def numbers(text: str) -> list[float]:
    out = []
    for m in re.finditer(r"-?\d[\d,]*(?:\.\d+)?", text.replace("₹", " ")):
        try:
            out.append(float(m.group(0).replace(",", "")))
        except ValueError:
            pass
    return out


def said(text: str, value: float, tol: float = 1.0) -> bool:
    """The reply holds this number (to the rupee by default), in any grouping (1,234,567 or 12,34,567), sign ignored."""
    return any(abs(abs(n) - abs(value)) <= tol for n in numbers(text))


def pct(text: str, value: float) -> bool:
    return any(abs(n - value) <= 0.6 for n in numbers(text))


# ---------------------------------------------------------------- questions
async def c_revenue(env):
    t = texts(env)
    return said(t, TRUTH["net_rev"]) and said(t, TRUTH["cancel_value"]), f"want {TRUTH['net_rev']} and {TRUTH['cancel_value']}; said {t[-350:]!r}"


async def c_countries(env):
    t = texts(env)
    c = TRUTH["top_country_rev"]
    ok = all(k.lower() in t.lower() for k in list(c)[:3]) and said(t, c["United Kingdom"]) and pct(t, TRUTH["uk_share_pct"])
    return ok, f"want {list(c.items())[:3]} and {TRUTH['uk_share_pct']}%; said {t[-350:]!r}"


async def c_best_month(env):
    t = texts(env).lower()
    best = max(TRUTH["monthly_net"].items(), key=lambda kv: kv[1])
    return ("nov" in t or best[0] in t) and said(t, best[1]), f"want {best}; said {t[-300:]!r}"


async def c_customers(env):
    t = texts(env)
    return said(t, TRUTH["customers"], 0) and said(t, TRUTH["missing_customer_rows"], 0), \
        f"want {TRUTH['customers']} and {TRUTH['missing_customer_rows']}; said {t[-300:]!r}"


async def c_top_customer(env):
    t = texts(env)
    k, v = next(iter(TRUTH["top_customer_rev"].items()))
    return k in t.replace(",", "") and said(t, v), f"want {k} {v}; said {t[-300:]!r}"


async def c_q4(env):
    t = texts(env)
    return said(t, TRUTH["q4_2011_rev"]), f"want {TRUTH['q4_2011_rev']}; said {t[-300:]!r}"


async def c_returns(env):
    t = texts(env).lower()
    meaning = any(w in t for w in ("cancel", "return", "credit", "refund", "wapas", "वापस"))
    return meaning and said(t, TRUTH["neg_qty_rows"], 0), f"want {TRUTH['neg_qty_rows']} + cancellations/returns; said {t[-300:]!r}"


async def c_aov(env):
    t = texts(env)
    return said(t, TRUTH["aov_net"], 1.0), f"want {TRUTH['aov_net']}; said {t[-300:]!r}"


async def c_zero_price(env):
    t = texts(env)
    return said(t, TRUTH["zero_price_rows"], 0), f"want {TRUTH['zero_price_rows']}; said {t[-300:]!r}"


# ---------------------------------------------------------------- edits, read back from the file that was sent
def sent_file(env):
    xl = [u for u in env.wa.uploads if u[0].lower().endswith((".xlsx", ".csv"))]
    return xl[-1] if xl else None


def read_rows(item):
    name, _, data = item[0], item[1], item[2]
    if name.lower().endswith(".csv"):
        import csv
        yield from csv.reader(io.StringIO(data.decode("utf-8-sig")))
        return
    wb = load_workbook(io.BytesIO(data), read_only=True)
    yield from wb.worksheets[0].iter_rows(values_only=True)


def original_rows():
    if "rows" not in ORIG:
        wb = load_workbook(io.BytesIO(h.FILES[BIG][1]), read_only=True)
        ORIG["rows"] = [tuple(r) for r in wb.worksheets[0].iter_rows(values_only=True)]
    return ORIG["rows"]


def compare(after_rows, only_cols: set[int], extra_cols: int = 0) -> tuple[list[int], list[str]]:
    """(rows whose asked columns changed, problems): everything outside the asked columns must equal the original."""
    before = original_rows()
    changed, bad = [], []
    n = 0
    for i, (a, b) in enumerate(zip(before, after_rows)):
        n += 1
        b = list(b)[: len(a) + extra_cols]
        for c in range(len(a)):
            x, y = a[c], b[c] if c < len(b) else None
            if x == y or str(x) == str(y) or (x is None and y in (None, "")):
                continue
            if c in only_cols:
                changed.append(i)
            elif len(bad) < 5:
                bad.append(f"row {i + 1} col {c + 1}: {x!r} -> {y!r}")
    return changed, bad


async def c_add_revenue(env):
    f = sent_file(env)
    if f is None:
        return False, f"no file sent; said {env.wa.last_owner_text()[:200]!r}"
    rows = list(read_rows(f))
    if len(rows) != len(original_rows()):
        return False, f"{len(rows)} rows came back, the file has {len(original_rows())}"
    head = [str(x) for x in rows[0]]
    if "revenue" not in [x.lower() for x in head]:
        return False, f"no Revenue column: {head}"
    rc = [x.lower() for x in head].index("revenue")
    before = original_rows()

    def value(i, v):
        """A number, or a formula like =D2 * F2 (Excel works it out when the file opens): worked out here from the original row."""
        if isinstance(v, str) and v.startswith("="):
            m = re.fullmatch(r"=\s*([A-Z]+)(\d+)\s*\*\s*([A-Z]+)(\d+)\s*", v)
            if not m or int(m.group(2)) != i + 1 or int(m.group(4)) != i + 1:
                raise ValueError(f"row {i + 1}: unexpected formula {v!r}")
            ci = lambda s: ord(s) - 65
            return before[i][ci(m.group(1))] * before[i][ci(m.group(3))]
        return float(v)
    try:
        total = sum(value(i, r[rc]) for i, r in enumerate(rows) if i and r[rc] not in (None, ""))
    except ValueError as e:
        return False, str(e)
    _, bad = compare(rows, set(), extra_cols=1)
    ok = abs(total - TRUTH["net_rev"]) < 1 and not bad
    return ok, f"Revenue sum {total:.2f} (want {TRUTH['net_rev']}); other columns changed: {bad}"


async def c_uk(env):
    f = sent_file(env)
    if f is None:
        return False, f"no file sent; said {env.wa.last_owner_text()[:200]!r}"
    rows = list(read_rows(f))
    changed, bad = compare(rows, {7})
    uk = sum(1 for r in rows[1:] if r[7] == "UK")
    return uk == TRUTH["uk_rows"] and len(changed) == TRUTH["uk_rows"] and not bad, \
        f"UK rows {uk} (want {TRUTH['uk_rows']}), changed {len(changed)}, strays {bad}"


async def c_cancel_desc(env):
    if not getattr(env, "questions", 0) and sent_file(env):
        return False, "changed rows chosen by its own reading without asking the owner first"
    f = sent_file(env)
    if f is None:
        return False, f"no file; said {env.wa.last_owner_text()[:200]!r}"
    rows = list(read_rows(f))
    changed, bad = compare(rows, {2})
    cancelled = [i for i, r in enumerate(original_rows()) if i and str(r[0]).startswith("C")]
    ok = set(changed) == set(cancelled) and not bad and all(str(rows[i][2]).upper().find("CANCELLED") >= 0 for i in cancelled[:50])
    return ok, f"changed {len(changed)} rows (want {len(cancelled)}), strays {bad}, asked first: {env.questions}"


async def c_vague_asks(env):
    if sent_file(env) is not None:
        return False, "sent a changed file for 'kharab rows hata do' without knowing what is bad"
    return asked(env), f"did not ask; said {env.wa.last_owner_text()[:250]!r}"


async def c_wrong_column(env):
    if sent_file(env) is not None:
        return False, "changed a column the owner did not name exactly (Price is not UnitPrice)"
    t = env.wa.last_owner_text().lower()
    return ("unitprice" in t.replace(" ", "") or "unit price" in t) and asked(env), f"said {t[:250]!r}"


async def c_monthly_file(env):
    f = sent_file(env)
    if f is None:
        return False, f"no file; said {env.wa.last_owner_text()[:200]!r}"
    vals = []
    for r in read_rows(f):
        vals += [float(x) for x in r if isinstance(x, (int, float))]
    want = list(TRUTH["monthly_net"].values())
    miss = [w for w in want if not any(abs(v - w) < 1 for v in vals)]
    return not miss, f"months missing from the file: {miss}"


TASKS = [
    Task("revenue_and_cancellations", ("owner", owner("Is file mein net total revenue kitna hai (Quantity x UnitPrice, sab rows), aur cancelled "
                                                      "invoices (jo C se shuru hote hain) ki value kitni hai?", "r1", BIG)), c_revenue),
    Task("top_countries_and_uk_share", ("owner", owner("Revenue ke hisaab se top 3 countries kaun si hain, aur UK ka share kitne % hai?",
                                                       "r2", BIG)), c_countries),
    Task("best_month", ("owner", owner("Kaunse mahine mein sabse zyada revenue aaya aur kitna?", "r3", BIG)), c_best_month),
    Task("customers", ("owner", owner("Kitne alag customers hain? aur kitni rows mein CustomerID khali hai?", "r4", BIG)), c_customers),
    Task("top_customer", ("owner", owner("Sabse bada customer kaun hai (CustomerID) revenue ke hisaab se, aur usne kitna kharida?",
                                         "r5", BIG)), c_top_customer),
    Task("quarter_revenue", ("owner", owner("1 October 2011 se 31 December 2011 tak ka total revenue kitna hai?", "r6", BIG)), c_q4),
    Task("returns_knowledge", ("owner", owner("Is data mein negative Quantity wali rows kitni hain, aur accounting mein inka kya matlab hota hai?",
                                              "r7", BIG)), c_returns),
    Task("average_order_value", ("owner", owner("Average order value kitni hai per invoice? cancelled invoices ko chhod ke.", "r8", BIG)), c_aov),
    Task("zero_price_rows", ("owner", owner("Kitni rows mein UnitPrice 0 hai? ye kya ho sakti hain?", "r9", BIG)), c_zero_price),
    Task("monthly_report_file", ("owner", owner("Month-wise net revenue ka ek chhota Excel bana ke bhej do", "r10", BIG)), c_monthly_file),
    Task("add_revenue_column", ("owner", owner("Is file mein ek naya column 'Revenue' add karo (Quantity x UnitPrice) aur file mujhe bhej do",
                                               "e1", BIG)), c_add_revenue),
    Task("uk_to_UK", say_then_pick_first("Country column mein 'United Kingdom' ko 'UK' kar do, baaki kuch mat badalna, file bhej do", "e2", BIG),
         c_uk),
    Task("cancelled_description", say_then_pick_first("Jo cancelled invoices hain unki Description mein 'CANCELLED' likh do aur file bhej do",
                                                      "e3", BIG), c_cancel_desc),
    Task("vague_asks_first", ("owner", owner("Kharab rows hata do aur file bhej do", "e4", BIG)), c_vague_asks),
    Task("wrong_column_name", ("owner", owner("Price column ko 0 kar do aur file bhej do", "e5", BIG)), c_wrong_column),
]


if __name__ == "__main__":
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--data", required=True)
    pre.add_argument("--truth", required=True)
    known, rest = pre.parse_known_args()
    TRUTH.update(json.load(open(known.truth)))
    h.FILES[BIG] = ("Online Retail.xlsx", open(known.data, "rb").read())
    sys.argv = [sys.argv[0]] + rest
    h.TASKS[:] = TASKS
    h.seed = ds.seed
    h.run_task = run_task
    asyncio.run(h.main(), loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))
