"""Read-only TallyPrime access through its local XML-over-HTTP interface (default 127.0.0.1:9000).

Safer than driving the UI for reading: structured, no focus/timing issues, no keystrokes
that could accept or delete a voucher. Only loopback is contacted and only Export
requests are built here; there is intentionally no import/write builder.
"""
from __future__ import annotations
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

DEFAULT_PORT = 9000
MAX_RESPONSE = 8_000_000
DATE = re.compile(r"\d{8}")
# Tally emits numeric references to control characters that XML 1.0 forbids.
BAD = re.compile(r"&#(?:0*(?:[0-8]|11|12|1[4-9]|2[0-9]|3[01]));|[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _head(kind, ident):
    return f"<ENVELOPE><HEADER><VERSION>1</VERSION><TALLYREQUEST>Export</TALLYREQUEST><TYPE>{kind}</TYPE><ID>{escape(ident)}</ID></HEADER><BODY><DESC>"


def build(report, company="", from_date="", to_date="", voucher_type=""):
    for d in (from_date, to_date):
        if d and not DATE.fullmatch(d): raise ValueError("Dates must be YYYYMMDD")
    static = "<SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>"
    if company: static += f"<SVCURRENTCOMPANY>{escape(company)}</SVCURRENTCOMPANY>"
    if from_date: static += f"<SVFROMDATE>{from_date}</SVFROMDATE>"
    if to_date: static += f"<SVTODATE>{to_date}</SVTODATE>"
    variables, tail = f"<STATICVARIABLES>{static}</STATICVARIABLES>", "</DESC></BODY></ENVELOPE>"
    if report == "companies": return _head("Collection", "List of Companies") + variables + tail
    if report in {"ledgers", "vouchers"}:
        kind, fetch, name = ("Ledger", "Name,Parent,OpeningBalance,ClosingBalance", "SmaraLedgers") if report == "ledgers" else \
            ("Voucher", "Date,VoucherTypeName,VoucherNumber,PartyLedgerName,Amount,Narration", "SmaraVouchers")
        filtered = report == "vouchers" and voucher_type
        collection = f'<COLLECTION NAME="{name}" ISMODIFY="No"><TYPE>{kind}</TYPE><FETCH>{fetch}</FETCH>' + ("<FILTERS>SmaraType</FILTERS>" if filtered else "") + "</COLLECTION>"
        system = '<SYSTEM TYPE="Formulae" NAME="SmaraType">$VoucherTypeName = "' + escape(voucher_type.replace('"', '')) + '"</SYSTEM>' if filtered else ""
        return _head("Collection", name) + variables + f"<TDL><TDLMESSAGE>{collection}{system}</TDLMESSAGE></TDL>" + tail
    if report in {"trial_balance", "day_book"}:
        return _head("Data", "Trial Balance" if report == "trial_balance" else "Day Book") + variables + tail
    raise ValueError("Unsupported report")


def parse(xml_bytes, limit=200):
    root = ET.fromstring(BAD.sub("", xml_bytes.decode("utf-8", "replace")))  # ElementTree never fetches external entities
    rows = []
    def visit(node):
        kids = list(node)
        if kids and all(len(k) == 0 for k in kids) and node.tag not in {"ENVELOPE", "BODY", "DATA", "COLLECTION", "IMPORTDATA", "DESC"}:
            rows.append({"_kind": node.tag, **{k.tag: (k.text or "").strip() for k in kids}, **node.attrib})
        else:
            for k in kids: visit(k)
    visit(root)
    return {"rows": rows[:limit], "row_count": len(rows), "truncated": len(rows) > limit}


def post(xml, port=DEFAULT_PORT, timeout=20):
    if not isinstance(port, int) or not 1 <= port <= 65535: raise ValueError("Invalid port")
    request = urllib.request.Request(f"http://127.0.0.1:{port}", data=xml.encode("utf-8"), headers={"Content-Type": "text/xml; charset=utf-8"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # never route loopback through a proxy
    try:
        with opener.open(request, timeout=timeout) as response: return response.read(MAX_RESPONSE)
    except (urllib.error.URLError, OSError):
        raise ValueError(f"Tally is not reachable on 127.0.0.1:{port}. Open TallyPrime, enable the HTTP server (F1 > Settings > Connectivity) and load a company.") from None


def read(args):
    if set(args) - {"report", "company", "from_date", "to_date", "voucher_type", "limit", "port"} or "report" not in args: raise ValueError("Invalid tally_read arguments")
    port, limit = args.get("port", DEFAULT_PORT), max(1, min(int(args.get("limit", 100)), 500))
    if args["report"] == "status": return {"reachable": True, **parse(post(build("companies"), port, timeout=5), limit)}
    raw = post(build(args["report"], args.get("company", ""), args.get("from_date", ""), args.get("to_date", ""), args.get("voucher_type", "")), port)
    return {"report": args["report"], "company": args.get("company", ""), "read_only": True, "untrusted": True, **parse(raw, limit)}
