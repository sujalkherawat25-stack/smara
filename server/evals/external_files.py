"""Smara on files made by other people: Tally's own sample import files, bank statement PDFs, and reconciliation workbooks
found online (not made by us). Exploratory: the replies are saved for a human to read; only a few answers have ground truth.

    EXT_DIR=/ext CAP_OUT=/out python -m evals.external_files --db postgresql://.../smara_eval --model glm5.3-flash \
        --sandbox container --jobs /jobs [--only a,b]
"""
import asyncio
import glob
import os
import selectors

from evals import agent_harness as h
from evals import demo_scenarios as ds
from evals.agent_harness import Task
from evals.capability_suite import cap, corpus, event, has_amt, seed

EXT = os.environ.get("EXT_DIR", "/ext")


def _load(key: str, pattern: str, name: str | None = None) -> None:
    paths = glob.glob(os.path.join(EXT, pattern), recursive=True)
    if paths:
        h.FILES[key] = (name or os.path.basename(paths[0]), open(paths[0], "rb").read())


_load("X01", "tally/x/Ledgers-in-Default-Template/*.xlsx")
_load("X02", "tally/x/Ledger-with-opening-balance-and-multiple-bill-wise-details/*.xlsx")
_load("X03", "tally/x/Sales-Voucher-with-bill-allocations/*.xlsx")
_load("X04", "tally/x/Ledger-Entries-in-Rows/*.xlsx")
_load("X05", "tally/x/Ledger-Entries-in-Columns/*.xlsx")
_load("X06", "tally/x/Amount-in-Same-Column/*.xlsx")
_load("X07", "tally/x/Sales-Voucher-in-Default-Template-with-GST*/*.xlsx")
_load("X08", "tally/x/Party-Ledgers-with-bank-details/*.xlsx")
_load("X09", "bank-pdf/type1_00001.pdf")
_load("X10", "bank-pdf/type2_00001.pdf")
_load("X11", "workbooks/busy_bank_reconciliation_format-1.xlsx")
_load("X13", "bank-pdf/type1_00002.pdf")
_load("X14", "bank-pdf/locked_type1_00003.pdf")
_load("X12", "workbooks/exceldemy_bank_reconciliation_statement.xlsx")

# type1_00001: transactions in the JSON that came with the PDF (credits / debits as the generator wrote them)
T1_CREDITS, T1_DEBITS, T1_ROWS = 607801.88, 673259.51, 162
# type2_00001: the PDF prints 158 rows (the JSON has 161): totals as read from the PDF's own rows, one failed cash withdrawal left out
T2_CREDITS, T2_DEBITS = 34173523.47, 34900186.69
CAUTION = ("match nahi", "add nahi", "tally nahi", "tie nahi", "does not add", "do not add", "doesn't add", "short", "gap", "farak", "fark", "inconsistent", "mismatch", "nahi mil")


def _says(env, n: int = 600) -> tuple[bool, str]:
    t = corpus(env)
    return len(t.strip()) > 40 and "error" not in t.lower()[:200], " ".join(t.split())[:n]


def reading(env):
    """No ground truth: the run passes when Smara answered with something; a human reads the saved reply."""
    return _says(env)


@cap
async def x_reading(env):
    return reading(env)


@cap
async def x_pdf_totals(env):
    t = corpus(env)
    ok_cr, ok_dr = has_amt(t, T1_CREDITS, tol=1.0), has_amt(t, T1_DEBITS, tol=1.0)
    return ok_cr and ok_dr, f"credits_ok={ok_cr} debits_ok={ok_dr} | " + " ".join(t.split())[:500]


@cap
async def x_pdf_type2(env):
    t = corpus(env)
    ok_cr, ok_dr = has_amt(t, T2_CREDITS), has_amt(t, T2_DEBITS)
    cautions = any(w in t.lower() for w in CAUTION)
    return ok_cr and ok_dr and cautions, f"credits_ok={ok_cr} debits_ok={ok_dr} says_it_does_not_tie={cautions} | " + " ".join(t.split())[:400]


@cap
async def x_password(env):
    """The locked statement: Smara must ask for the password, then (given it) quote the program-read totals."""
    import tempfile
    from sandbox import smara_helpers as mh
    with tempfile.TemporaryDirectory() as d:
        open(d + "/x.pdf", "wb").write(h.FILES["X14"][1])
        truth = mh.bank_statement_pdf(d + "/x.pdf", "Test@1234")
    t = corpus(env)
    ok_cr, ok_dr = has_amt(t, truth["credits"]["total"]), has_amt(t, truth["debits"]["total"])
    asked = "password" in t.lower()
    return asked and ok_cr and ok_dr, f"asked={asked} credits_ok={ok_cr} debits_ok={ok_dr} | " + " ".join(t.split())[:300]


def tasks():
    P = event
    A = lambda name, q, *files: Task(name, ("custom", P(*[(q, f) for f in files] if files else [(q,)])), x_reading)
    return [
        A("ext_tally_ledgers_default", "Ye Tally se party ledgers aur unke bills nikale hain. Import karke batao kul outstanding kitna hai aur kaunsi party ka kitna.", "X01"),
        A("ext_tally_opening_bills", "Ye Tally ledger hai opening balance aur bill-wise details ke saath. Import karo aur batao kitna baaki hai.", "X02"),
        A("ext_tally_sales_bill_alloc", "Ye Tally ke sales vouchers hain. Party-wise total sales batao aur kitna GST laga.", "X03"),
        A("ext_tally_entries_rows", "Ye Tally ki sales bills hain (har ledger alag row mein). Party-wise sales total aur kul tax batao.", "X04"),
        A("ext_tally_entries_columns", "Ye sales bills ki list hai. Item-wise kitna bika aur party-wise total batao.", "X05"),
        A("ext_tally_cash_bank_entries", "Ye cash aur bank ki entries hain Tally se. Kul kharcha aur kaunse ledger mein kitna gaya?", "X06"),
        A("ext_tally_gst_einvoice", "Ye sales vouchers GST aur e-invoice details ke saath hain. GSTIN-wise kitni sales hui aur kitna CGST/SGST/IGST laga?", "X07"),
        A("ext_tally_party_bank_details", "Ye party ledgers import kar do aur batao kis party ka kitna outstanding hai.", "X08"),
        Task("ext_bank_pdf_type1", ("custom", P(("Ye bank statement (PDF) hai. Kul credits aur kul debits kitne hain aur top 5 credits kaunse hain? Opening aur closing balance se match bhi karke batao.", "X09"))), x_pdf_totals),
        Task("ext_bank_pdf_type2", ("custom", P(("Ye bank statement (PDF) hai. Kul credits, kul debits aur top 5 kharche batao. Opening aur closing se match karta hai?", "X10"))), x_pdf_type2),
        A("ext_bank_pdf_expense_split", "Ye bank statement hai. Debits ko category mein baanto (rent, salary, GST payment, UPI, cheque, charges) aur har category ka total batao.", "X13"),
        Task("ext_bank_pdf_password", ("custom", P(("Ye bank statement hai, kul credits aur debits batao.", "X14"), ("Password hai Test@1234", None))), x_password),
        A("ext_busy_bank_reco", "Ye bank reconciliation sheet hai. Kya baaki (unreconciled) hai, kitna, aur kaunsi entries?", "X11"),
        A("ext_exceldemy_brs", "Ye bank reconciliation statement hai. Check karo: balances sahi match ho rahe hain? Koi galti?", "X12"),
    ]


TASKS = tasks()

if __name__ == "__main__":
    h.TASKS[:] = TASKS
    h.seed = seed
    h.run_task = ds.run_task
    asyncio.run(h.main(), loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))
