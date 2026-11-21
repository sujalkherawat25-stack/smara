"""Second, independent derivation of the answer keys in ca_data.py: it reads only the generated files (with pandas) and works the
figures out again by its own route. If the two disagree the key is wrong and the eval must not be trusted.

    python -m evals.verify_truth
"""
import io
import re
import sys

import pandas as pd

from evals import ca_data as d


def table(files, name, first_col, sheet=0):
    raw = pd.read_excel(io.BytesIO(files[name]), sheet_name=sheet, header=None)
    hr = raw.index[raw.iloc[:, 0].astype(str).str.strip() == first_col][0]
    t = raw.iloc[hr + 1:].copy()
    t.columns = [str(x).strip() for x in raw.iloc[hr]]
    return t.dropna(how="all").reset_index(drop=True)


def norm(s):
    s = re.sub(r"[^A-Z0-9]", "", str(s).upper())
    return re.sub(r"(?<=[A-Z])0+(?=\d)", "", s)


def close(a, b, tol=0.01):
    return abs(float(a) - float(b)) <= tol


def check(label, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + label + ("" if ok else f"   {detail}"))
    return ok


def half_up(x):
    return float(int(x + 0.5 + 1e-9))


def main():
    good = True
    # 1. purchase register vs GSTR-2B ------------------------------------------------------------------------------
    f, t = d.gst_pair()
    b = table(f, "purchase_register_oct.xlsx", "Voucher Date")
    g = table(f, "GSTR2B_102026.xlsx", "GSTIN of supplier", sheet="B2B")
    b["key"] = b["Supplier GSTIN"] + "|" + b["Invoice No"].map(norm)
    g["key"] = g["GSTIN of supplier"] + "|" + g["Invoice number"].map(norm)
    dup_extra = b[b.duplicated("key", keep="first")]
    first = b.drop_duplicates("key", keep="first")
    miss = first[~first.key.isin(g.key)]
    only = g[~g.key.isin(first.key)]
    both = first.merge(g, on="key", suffixes=("_b", "_g"))
    mism = both[(both["Taxable Value"].astype(float) - both["Taxable Value (Rs)"].astype(float)).abs() > 1]
    tax_b = lambda df: (df["CGST"].astype(float) + df["SGST"].astype(float) + df["IGST"].astype(float)).sum()
    tax_g = lambda df: (df["Integrated Tax(Rs)"].astype(float) + df["Central Tax(Rs)"].astype(float) + df["State/UT Tax(Rs)"].astype(float)).sum()
    kw = ("motor car", "gift", "canteen", "cab hire", "club")
    blk = both[both["Narration"].str.lower().apply(lambda s: any(k in s for k in kw))]
    itc_no = both[both["ITC Availability"] == "No"]
    ok_rows = both[(both["ITC Availability"] == "Yes") & ~both.key.isin(blk.key)]
    spelled = both[both["Invoice No"] != both["Invoice number"]]
    good &= check("gst missing in 2B", len(miss) == 14 and close(tax_b(miss), t["missing_in_2b"]["itc"]), (len(miss), tax_b(miss)))
    good &= check("gst only in 2B", len(only) == 9 and close(tax_g(only), t["only_in_2b"]["itc"]), (len(only), tax_g(only)))
    good &= check("gst value mismatch", len(mism) == 8, len(mism))
    good &= check("gst number written differently", len(spelled) == 11, len(spelled))
    good &= check("gst double booked", len(dup_extra) == 4, len(dup_extra))
    good &= check("gst blocked credit", len(blk) == 5 and close(tax_b(blk), t["blocked"]["itc"]), (len(blk), tax_b(blk)))
    good &= check("gst 2B says ITC not available", len(itc_no) == 6 and close(tax_g(itc_no), t["itc_no"]["itc"]), len(itc_no))
    good &= check("gst claimable ITC", close(tax_g(ok_rows), t["claimable_itc"]), (tax_g(ok_rows), t["claimable_itc"]))
    # 2. sales register ---------------------------------------------------------------------------------------------
    f, t = d.sales_register()
    s = table(f, "sales_register_q2.xlsx", "Invoice No")
    for c in ("Taxable Value", "CGST", "SGST", "IGST", "Invoice Value"):
        s[c] = s[c].astype(float)

    def gs_ok(x):
        return isinstance(x, str) and len(x) == 15 and x == d.gstin(x[:2], x[2:12])
    dups = s[s.duplicated(keep="first")]
    u = s.drop_duplicates(keep="first")
    intra = u["Place of Supply"] == "Delhi"
    cn = u["Invoice No"].str.startswith("CN/")
    res = {
        "wrong_head_igst_on_intra": u[(u["Type"] == "B2B") & intra & (u["IGST"] != 0)],
        "wrong_head_cgst_sgst_on_inter": u[(u["Type"] == "B2B") & ~intra & ((u["CGST"] != 0) | (u["SGST"] != 0))],
        "total_mismatch": u[((u["Taxable Value"] + u["CGST"] + u["SGST"] + u["IGST"]) - u["Invoice Value"]).abs() > 0.5],
        "b2b_missing_gstin": u[(u["Type"] == "B2B") & (u["Customer GSTIN"].fillna("") == "")],
        "invalid_gstin": u[(u["Type"] == "B2B") & (u["Customer GSTIN"].fillna("") != "") & ~u["Customer GSTIN"].map(gs_ok)],
        "rate_mismatch": u[(((u["CGST"] + u["SGST"] + u["IGST"]) - u["Taxable Value"] * u["GST Rate %"].astype(float) / 100).abs() > 1) & ~cn],
        "duplicates": dups,
    }
    for k, v in res.items():
        have = sorted(v["Invoice No"])
        good &= check("sales " + k, have == t[k], (len(have), len(t[k]), sorted(set(have) ^ set(t[k]))[:4]))
    # 3. bank reconciliation, from the two files only --------------------------------------------------------------
    f, t = d.bank_recon()
    bk = table(f, "bank_book_oct.xlsx", "Date")
    st = table(f, "bank_statement_oct.xlsx", "Txn Date")
    for c in ("Receipt (Dr)", "Payment (Cr)"):
        bk[c] = bk[c].astype(float).fillna(0)
    for c in ("Withdrawal", "Deposit"):
        st[c] = st[c].astype(float).fillna(0)
    books = 500000 + bk["Receipt (Dr)"].sum() - bk["Payment (Cr)"].sum()
    bank = float(st["Closing Balance"].astype(float).iloc[-1])
    good &= check("bank: balance per cash book", close(books, t["books_balance"]), books)
    good &= check("bank: balance per statement", close(bank, t["bank_balance"]), bank)
    good &= check("bank: statement runs correctly", close(500000 + st["Deposit"].sum() - st["Withdrawal"].sum(), bank))
    refs = set(st["Chq/Ref No"].dropna().astype(str))
    pay = bk[bk["Payment (Cr)"] > 0]
    each = pay.groupby(pay["Voucher / Cheque No"].astype(str))["Payment (Cr)"].agg(["count", "sum"])
    unc = each[~each.index.isin(refs)]
    good &= check("bank: cheques not yet presented", len(unc) == t["uncleared_n"] and close(unc["sum"].sum(), t["uncleared_cheques"]), (len(unc), unc["sum"].sum()))
    twice = each[each["count"] == 2]
    good &= check("bank: payment entered twice", len(twice) == 1 and close(twice["sum"].iloc[0] / 2, t["duplicate_payment"]), twice.to_dict())
    rec = bk[(bk["Receipt (Dr)"] > 0)]
    sref = st[st["Deposit"] > 0].set_index(st[st["Deposit"] > 0]["Chq/Ref No"].astype(str))["Deposit"]
    dit = rec[~rec["Voucher / Cheque No"].astype(str).isin(sref.index)]
    good &= check("bank: deposits in transit", len(dit) == t["deposits_n"] and close(dit["Receipt (Dr)"].sum(), t["deposits_in_transit"]), (len(dit), dit["Receipt (Dr)"].sum()))
    diff = [(r["Voucher / Cheque No"], r["Receipt (Dr)"], sref[str(r["Voucher / Cheque No"])]) for _, r in rec.iterrows()
            if str(r["Voucher / Cheque No"]) in sref.index and abs(r["Receipt (Dr)"] - sref[str(r["Voucher / Cheque No"])]) > 0.5]
    good &= check("bank: receipt recorded at a different amount", len(diff) == 1 and diff[0][1] == 38160 and diff[0][2] == 38610, diff)
    only_bank = st[st["Chq/Ref No"].isna()]
    good &= check("bank: items only the bank knows", close(only_bank["Withdrawal"].sum(), t["bank_charges"]) and close(only_bank["Deposit"].sum(), t["interest"] + t["direct_credit"]),
                  (only_bank["Withdrawal"].sum(), only_bank["Deposit"].sum()))
    adj = books + 450 + t["duplicate_payment"] - only_bank["Withdrawal"].sum() + only_bank["Deposit"].sum()
    side = bank + dit["Receipt (Dr)"].sum() - unc["sum"].sum()
    good &= check("bank: both sides meet at the reconciled balance", close(adj, side) and close(adj, t["reconciled_balance"]), (adj, side))
    # 4. trial balance ----------------------------------------------------------------------------------------------
    f, t = d.trial_balance()
    tb = table(f, "trial_balance_oct.xlsx", "Account", sheet="Trial Balance")
    j = table(f, "trial_balance_oct.xlsx", "Voucher No", sheet="Journal")
    j["Debit"], j["Credit"] = j["Debit"].astype(float).fillna(0), j["Credit"].astype(float).fillna(0)
    net = j.groupby("Account")["Debit"].sum() - j.groupby("Account")["Credit"].sum()
    bad = []
    for _, r in tb[tb["Account"] != "Total"].iterrows():
        shown = (0 if pd.isna(r["Debit"]) else float(r["Debit"])) - (0 if pd.isna(r["Credit"]) else float(r["Credit"]))
        if abs(shown - net.get(r["Account"], 0)) > 0.5:
            bad.append((r["Account"], shown, net.get(r["Account"], 0)))
    good &= check("tb: the one account that disagrees with the journal", len(bad) == 1 and bad[0][0] == t["account"] and close(bad[0][1], t["tb_shows"]) and close(bad[0][2], t["journal_total"]), bad)
    # 5. ageing -----------------------------------------------------------------------------------------------------
    f, t = d.ageing()
    a = table(f, "receivables_open.xlsx", "Invoice No")
    a["days"] = (pd.Timestamp("2026-10-31") - pd.to_datetime(a["Invoice Date"])).dt.days
    a["b"] = pd.cut(a["days"], [-1, 30, 60, 90, 10_000], labels=["0-30", "31-60", "61-90", "90+"])
    tot = a.groupby("b", observed=True)["Outstanding Amount"].sum().astype(float)
    good &= check("ageing buckets", all(close(tot[k], v) for k, v in t["buckets"].items()), dict(tot))
    p90 = a[a.b == "90+"].groupby("Party")["Outstanding Amount"].sum().astype(float).sort_values(ascending=False).head(3)
    good &= check("ageing top three in 90+", list(p90.index) == t["top90"], list(p90.index))
    # 6. TDS --------------------------------------------------------------------------------------------------------
    f, t = d.tds()
    p = table(f, "vendor_payments_tds.xlsx", "Voucher")

    def rate(r):
        return (1 if r["Vendor Type"] == "Individual" else 2) if r["Section"] == "194C" else {"194J": 10, "194I": 10, "194H": 2}[r["Section"]]

    def want(r):
        gross = float(r["Gross Amount"])
        if r["Section"] == "194C" and gross <= 30000:
            return 0.0
        pct = rate(r) if isinstance(r["PAN"], str) and r["PAN"].strip() else max(rate(r), 20)
        return half_up(gross * pct / 100)
    p["want"] = p.apply(want, axis=1)
    p["got"] = p["TDS Deducted"].astype(float)
    short = p[p.got < p.want - 0.5]
    good &= check("tds expected and deducted totals", close(p.want.sum(), t["expected_total"]) and close(p.got.sum(), t["deducted_total"]), (p.want.sum(), p.got.sum()))
    good &= check("tds short deduction", len(short) == t["short_n"] and close((short.want - short.got).sum(), t["short_total"]), (len(short), (short.want - short.got).sum()))
    good &= check("tds excess deduction", len(p[p.got > p.want + 0.5]) == t["excess_n"], len(p[p.got > p.want + 0.5]))
    # 7. payroll ----------------------------------------------------------------------------------------------------
    f, t = d.payroll()
    s = table(f, "salary_oct.xlsx", "Emp ID")
    mism = 0
    for _, r in s.iterrows():
        fr = (30 - float(r["LOP Days"])) / 30
        eg = half_up((float(r["Basic"]) + float(r["HRA"]) + float(r["Special Allowance"])) * fr)
        eb = float(r["Basic"]) * fr
        pf = half_up(min(eb, 15000) * 0.12)
        esi = half_up(eg * 0.0075) if eg <= 21000 else 0
        k = t["rows"][r["Emp ID"]]
        if abs(eg - k["earned_gross"]) > 1 or abs(pf - k["pf"]) > 1 or abs(esi - k["esi"]) > 1:
            mism += 1
    good &= check("payroll rows agree", mism == 0, mism)
    # 8. depreciation -----------------------------------------------------------------------------------------------
    f, t = d.depreciation()
    a = table(f, "asset_register.xlsx", "Asset No")
    dep = 0
    for _, r in a.iterrows():
        amt, rt = float(r["Amount (Opening WDV or Cost)"]), d.DEP_RATES[r["Class"]]
        if r["Type"] == "Addition" and pd.Timestamp(r["Date Put To Use"]) >= pd.Timestamp("2026-10-04"):
            rt /= 2
        dep += half_up(amt * rt / 100)
    good &= check("depreciation total", close(dep, t["dep_total"], 1), (dep, t["dep_total"]))
    # 9. GST liability inputs ---------------------------------------------------------------------------------------
    f, t = d.liability()
    s = table(f, "client_books_oct.xlsx", "Invoice", sheet="Sales")
    pu = table(f, "client_books_oct.xlsx", "Invoice", sheet="Purchases")
    out = {k: s[c].astype(float).sum() for k, c in (("c", "CGST"), ("s", "SGST"), ("i", "IGST"))}
    el = pu[pu["ITC Eligible"] == "Yes"]
    itc = {k: el[c].astype(float).sum() for k, c in (("c", "CGST"), ("s", "SGST"), ("i", "IGST"))}
    good &= check("liability: output tax", all(close(out[k], t["output"][k], 0.05) for k in out), out)
    good &= check("liability: eligible ITC", all(close(itc[k], t["itc_eligible"][k], 0.05) for k in itc), itc)
    # set-off again, written differently: a tiny solver over the allowed (credit -> liability) moves in the statutory order
    liab, cr = dict(out), dict(itc)
    for frm, order in (("i", ("i", "c", "s")), ("c", ("c", "i")), ("s", ("s", "i"))):
        for to in order:
            u_ = min(cr[frm], liab[to])
            cr[frm] -= u_
            liab[to] -= u_
    good &= check("liability: cash payable after set-off", all(close(liab[k], t["cash_payable"][k], 0.05) for k in liab), liab)
    # 10. party names -----------------------------------------------------------------------------------------------
    f, t = d.dirty_parties()
    l = table(f, "sales_ledger_oct.xlsx", "Invoice")
    l["key"] = l["Party"].astype(str).str.upper().str.replace(r"^(M/S|MESSRS)\s+", "", regex=True).str.replace(r"[^A-Z0-9 ]", "", regex=True).str.strip()
    gr = l.groupby("key")["Amount"].sum().astype(float)
    good &= check("dirty names: obvious variants merge to the keyed totals", all(close(gr[k.upper()], v) for k, v in t["merged"].items()), "")
    print("\nALL KEYS VERIFIED" if good else "\nSOME KEYS DISAGREE - fix before running the eval")
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
