"""TDS check on a table of payments in one call: what should have been deducted, what was, where it is short and where it is too much.

The OWNER'S rules are the input (rates per section, the vendor-type rate where it differs, a per-payment threshold, the no-PAN rate):
the tool does the arithmetic and never decides a rate. Rules it applies, in this order, for each payment:
  1. the section has no rate given -> the row is listed as "no rate given", not guessed
  2. the payment is at or below the section's threshold -> no TDS (even without a PAN: the higher no-PAN rate applies only where TDS applies)
  3. otherwise the rate (by vendor type when given as a table); no PAN (blank, NA) -> the higher of that rate and the no-PAN rate
  4. whole rupees, half up
Short = deducted is more than Rs 1 below what was due; excess = more than Rs 1 above. It reads the file and changes nothing.
"""
import re
from decimal import ROUND_HALF_UP, Decimal

from src.agent import ca_reports
from src.agent.ca_reports import rs
from src.ingest.sheet_mapper import _norm, parse_amount_paise, parse_date, read_named_tables

HEADER_SCAN = 40
FIELDS = {
    "voucher": ("voucher", "voucher no", "vch no", "payment no", "ref", "ref no", "invoice no", "bill no"),
    "date": ("date", "payment date", "voucher date", "bill date"),
    "vendor": ("vendor", "vendor name", "party", "party name", "deductee", "deductee name", "supplier", "supplier name", "name"),
    "vendor_type": ("vendor type", "deductee type", "type", "category", "status", "deductee status"),
    "pan": ("pan", "pan no", "pan number", "deductee pan"),
    "section": ("section", "tds section", "sec", "nature of payment"),
    "amount": ("gross amount", "amount", "payment", "gross", "amount paid", "payment amount", "gross payment"),
    "deducted": ("tds deducted", "tds", "tds amount", "tax deducted", "tds deducted amount", "tds deducted rs"),
}


def _map(header: list) -> dict:
    cells = [_norm(h) for h in header]
    found: dict[str, int] = {}
    for exact in (True, False):
        for i, cell in enumerate(cells):
            if not cell or i in found.values():
                continue
            for field, names in FIELDS.items():
                if field not in found and ((cell in names) if exact else any(len(n) >= 5 and cell.startswith(n + " ") for n in names)):
                    found[field] = i
                    break
    return found


def parse_file(filename: str, data: bytes) -> dict:
    try:
        tables = read_named_tables(filename, data)
    except Exception as e:
        return {"error": f"could not open {filename}: {str(e)[:120]}"}
    for name, rows in tables:
        for i, row in enumerate(rows[:HEADER_SCAN]):
            cols = _map(row)
            if {"section", "amount", "deducted"} <= set(cols):
                out = []
                for n, r in enumerate(rows[i + 1:], start=i + 2):
                    g = lambda f: r[cols[f]] if f in cols and cols[f] < len(r) else None
                    amt = parse_amount_paise(g("amount"))
                    sec = str(g("section") or "").strip().upper()
                    if amt is None or not sec or re.match(r"^\s*(grand\s+)?total\b", str(g("vendor") or g("voucher") or ""), re.I):
                        continue
                    d = parse_date(g("date"))
                    out.append({"row": n, "voucher": str(g("voucher") or "").strip(), "date": d.isoformat() if d else "", "vendor": str(g("vendor") or "").strip(),
                                "vendor_type": str(g("vendor_type") or "").strip(), "pan": str(g("pan") or "").strip(), "section": sec, "amount": amt,
                                "deducted": parse_amount_paise(g("deducted")) or 0})
                return {"rows": out, "cols": set(cols), "sheet": name, "filename": filename}
    return {"error": f"{filename}: could not find a header with a section, an amount and a TDS deducted column"}


def _rate(rates: dict, section: str, vendor_type: str):
    """The rate for this section and vendor type. A table by vendor type may key a rate as the owner wrote it: 'Individual/HUF' or 'Individual, HUF'
    covers both, case does not matter, and 'others'/'default' catches the rest. None when nothing matches (the row is then listed, not guessed)."""
    r = rates.get(section)
    if not isinstance(r, dict):
        return r
    table = {}
    for key, value in r.items():
        for part in re.split(r"\s*(?:/|,|&|\bor\b|\band\b)\s*", str(key).strip().lower()):
            if part:
                table[part] = value
    vt = vendor_type.strip().lower()
    if vt in table:
        return table[vt]
    if len(vt) >= 3:
        for part, value in table.items():                 # 'Individuals' / 'Indiv.' for 'individual'
            if len(part) >= 3 and (vt.startswith(part) or part.startswith(vt)):
                return value
    return next((table[k] for k in ("others", "other", "default", "all", "rest") if k in table), None)


def why_no_rate(rates: dict, row: dict) -> str:
    r = rates.get(row["section"])
    if isinstance(r, dict):
        given = ", ".join(str(k) for k in r)
        return f"vendor type '{row['vendor_type'] or 'blank'}' is not in the {row['section']} rates you gave ({given})"
    return f"no rate was given for section {row['section']}"


def check(rows: list[dict], rates: dict, thresholds: dict, no_pan_rate: float) -> dict:
    rates = {str(k).strip().upper(): v for k, v in (rates or {}).items()}
    th = {str(k).strip().upper(): float(v) * 100 for k, v in (thresholds or {}).items()}          # rupees -> paise
    out, no_rate = [], []
    for r in rows:
        rate = _rate(rates, r["section"], r["vendor_type"])
        if rate is None:
            no_rate.append({**r, "why": why_no_rate(rates, r)})
            continue
        if r["section"] in th and r["amount"] <= th[r["section"]]:
            due, why = 0, f"at or below the {r['section']} threshold"
        else:
            has_pan = r["pan"].strip().upper() not in ("", "NA", "N/A", "NOT AVAILABLE", "NONE", "-", "NAN")
            pct = float(rate) if has_pan else max(float(rate), float(no_pan_rate))
            due = int((Decimal(r["amount"]) / 100 * Decimal(str(pct)) / 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)) * 100
            why = f"{pct:g}%" + ("" if has_pan else " (no PAN)")
        out.append({**r, "due": due, "why": why, "diff": due - r["deducted"]})
    short = [x for x in out if x["diff"] > 100]
    excess = [x for x in out if x["diff"] < -100]
    return {"rows": out, "short": short, "excess": excess, "no_rate": no_rate}


def _row(x):
    return {"voucher": x["voucher"], "vendor": x["vendor"], "section": x["section"], "gross_rupees": x["amount"] / 100, "due_rupees": x["due"] / 100,
            "deducted_rupees": x["deducted"] / 100, "difference_rupees": x["diff"] / 100, "basis": x["why"]}


def summary_lines(res: dict, rules: dict) -> list[str]:
    due, ded = sum(x["due"] for x in res["rows"]) / 100, sum(x["deducted"] for x in res["rows"]) / 100
    s, e = res["short"], res["excess"]
    L = [f"Checked {len(res['rows'])} payments with the rules you gave: {rules['summary']}.",
         f"TDS that should have been deducted: {rs(due)}. Deducted: {rs(ded)}.",
         f"Short deducted: {len(s)} payments, {rs(sum(x['diff'] for x in s) / 100)}." + (" Examples: " + "; ".join(f"{x['voucher']} {x['vendor']} {x['section']} short {rs(x['diff'] / 100)} ({x['why']})" for x in sorted(s, key=lambda x: -x["diff"])[:4]) + "." if s else ""),
         f"Deducted too much: {len(e)} payments, {rs(-sum(x['diff'] for x in e) / 100)}." + (" Examples: " + "; ".join(f"{x['voucher']} {x['vendor']} {x['section']} excess {rs(-x['diff'] / 100)}" for x in sorted(e, key=lambda x: x['diff'])[:4]) + "." if e else "")]
    if res["no_rate"]:
        reasons: dict[str, int] = {}
        for x in res["no_rate"]:
            reasons[x["why"]] = reasons.get(x["why"], 0) + 1
        L.append(f"{len(res['no_rate'])} payments have NO rate and are left OUT of the totals above, so they are incomplete: " + "; ".join(f"{n} where {why}" for why, n in reasons.items())
                 + ". Say this to the owner with the reason, and ask for the missing rate.")
    return L


async def tds_check(app, ctx, args: dict) -> dict:
    from src.agent import actions
    ref = args.get("file_ref")
    if not isinstance(ref, str) or not ref:
        return {"error": "give file_ref: the payments table (voucher, vendor, PAN, section, gross amount, TDS deducted) as Excel or CSV"}
    rates = args.get("rates")
    if not isinstance(rates, dict) or not rates:
        return {"error": "give rates: the owner's rate per section, e.g. {\"194C\": {\"Individual\": 1, \"Company\": 2}, \"194J\": 10}. If they did not say, ask them (or the CA): do not guess a rate."}
    th = args.get("thresholds") if isinstance(args.get("thresholds"), dict) else {}
    try:
        no_pan = float(args.get("no_pan_rate") if args.get("no_pan_rate") is not None else 20)
    except (TypeError, ValueError):
        return {"error": "no_pan_rate must be a number such as 20"}
    data, _, name = await actions._fetch(app, ref, ctx)
    p = parse_file(name, data)
    if "error" in p:
        return {"error": p["error"], "hint": "ask the owner for the payments with section, gross amount and TDS deducted columns"}
    res = check(p["rows"], rates, th, no_pan)
    rules = {"summary": "; ".join(f"{k} {v if not isinstance(v, dict) else ', '.join(f'{a} {b}%' for a, b in v.items())}" + ("%" if not isinstance(v, dict) else "") for k, v in rates.items())
             + (f"; no TDS at or below {', '.join(f'{k} Rs {v:,.0f}' for k, v in th.items())}" if th else "; NO per-payment limit was given, so payments of every size are taxed")
             + f"; no PAN: {no_pan:g}% or the rate if higher"}
    out = {"summary_lines": summary_lines(res, rules), "rules_used": rules["summary"], "payments_checked": len(res["rows"]),
           "expected_tds_rupees": sum(x["due"] for x in res["rows"]) / 100, "deducted_tds_rupees": sum(x["deducted"] for x in res["rows"]) / 100,
           "short_count": len(res["short"]), "short_rupees": sum(x["diff"] for x in res["short"]) / 100,
           "excess_count": len(res["excess"]), "excess_rupees": -sum(x["diff"] for x in res["excess"]) / 100,
           "short": [_row(x) for x in sorted(res["short"], key=lambda x: -x["diff"])], "excess": [_row(x) for x in sorted(res["excess"], key=lambda x: x["diff"])],
           "no_rate_given_rows": len(res["no_rate"])}
    if args.get("report", True):
        cols = ["Voucher", "Vendor", "Section", "Gross (Rs)", "Due (Rs)", "Deducted (Rs)", "Difference (Rs)", "Basis"]
        tab = lambda xs: [[x["voucher"], x["vendor"], x["section"], x["amount"] / 100, x["due"] / 100, x["deducted"] / 100, x["diff"] / 100, x["why"]] for x in xs]
        sheets = [("Short deducted", cols, tab(sorted(res["short"], key=lambda x: -x["diff"]))), ("Deducted too much", cols, tab(sorted(res["excess"], key=lambda x: x["diff"]))),
                  ("All payments", cols, tab(res["rows"])), ("Rules used", ["Rule"], [[rules["summary"]]])]
        money = lambda cs: [{"label": c, "kind": "money", "align": "right"} if c.endswith("(Rs)") else {"label": c} for c in cs]
        blocks = [{"type": "stats", "items": [{"label": "Should have been", "value": rs(out["expected_tds_rupees"])}, {"label": "Deducted", "value": rs(out["deducted_tds_rupees"])},
                                              {"label": "Short", "value": f"{len(res['short'])} / {rs(out['short_rupees'])}", "tone": "bad" if res["short"] else "good"},
                                              {"label": "Too much", "value": f"{len(res['excess'])} / {rs(out['excess_rupees'])}", "tone": "bad" if res["excess"] else "good"}]},
                  {"type": "note", "text": "Rules applied: " + rules["summary"] + ". Prepared for the owner and the CA to review; nothing in the file was changed."}]
        if res["short"]:
            blocks.insert(1, {"type": "table", "caption": "Short deducted", "columns": money(cols), "rows": tab(sorted(res["short"], key=lambda x: -x["diff"]))[:40]})
        d = await ca_reports.deliver(app, ctx, title="TDS check", subtitle=name, blocks=blocks, xl_name="TDS_check.xlsx", sheets=sheets, caption="TDS check", send=bool(args.get("send", True)))
        out.update({"files_made": d["files"], "files_sent_to_owner": d["sent"], "files_not_sent": d["not_sent"], "_new_files": d["_new_files"], "file_refs": d["file_refs"]})
    out["next"] = ("Tell the owner the result in their language using summary_lines (the figures are final: quote them, never recompute them or redo this with code). "
                   + ("The PDF and the full Excel (short, excess and every payment) have already been sent. " if out.get("files_sent_to_owner") else "")
                   + "FIRST compare rules_used with what the owner said: every rate, any limit below which a payment has no TDS ('30,000 ya kam pe TDS nahi'), the no-PAN rate. "
                     "If something the owner said is missing or different there, call tds_check again with the corrected rules BEFORE you answer, and never tell the owner a rule "
                     "was applied that rules_used does not show. Then state the rules used and ask the CA to confirm them; a payment at or below a limit has no TDS even without a PAN.")
    return ca_reports.compact(out, ("summary_lines", "next", "files_sent_to_owner", "files_not_sent", "rules_used", "payments_checked", "expected_tds_rupees", "deducted_tds_rupees",
                                    "short_count", "short_rupees", "excess_count", "excess_rupees"), cap=6)
