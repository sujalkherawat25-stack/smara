"""Nightly Tally -> Smara sync, run by the helper on the owner's PC.

Reads open bills from the local TallyPrime (127.0.0.1 only, Export requests only) and posts them to the shop's own
Smara server with the helper's device token. Nothing is written back to Tally and nothing is sent to anyone.
Receivables become collection reminders (the owner still approves every message); payables feed supplier deadlines.
"""
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from xml.sax.saxutils import escape

import httpx

BAD = re.compile(r"&#(?:0*(?:[0-8]|11|12|1[4-9]|2[0-9]|3[01]));|[\x00-\x08\x0b\x0c\x0e-\x1f]")
DEFAULT_PORT = 9000
RUN_AFTER_HOUR = 2          # local time; one run per day
MAX_RESPONSE = 20_000_000
CHUNK = 5000                # the server accepts at most this many rows of each kind per call


def _statics(company: str) -> str:
    return "<SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>" + (f"<SVCURRENTCOMPANY>{escape(company)}</SVCURRENTCOMPANY>" if company else "")


def _request(report_id: str, company: str = "") -> str:
    return (f"<ENVELOPE><HEADER><VERSION>1</VERSION><TALLYREQUEST>Export</TALLYREQUEST><TYPE>Data</TYPE><ID>{escape(report_id)}</ID></HEADER>"
            f"<BODY><DESC><STATICVARIABLES>{_statics(company)}</STATICVARIABLES></DESC></BODY></ENVELOPE>")


def _ledger_request(company: str = "") -> str:
    coll = ('<COLLECTION NAME="SmaraParties" ISMODIFY="No"><TYPE>Ledger</TYPE>'
            '<FETCH>Name,Parent,LedgerMobile,LedgerPhone,PartyGSTIN</FETCH></COLLECTION>')
    return ('<ENVELOPE><HEADER><VERSION>1</VERSION><TALLYREQUEST>Export</TALLYREQUEST><TYPE>Collection</TYPE><ID>SmaraParties</ID></HEADER>'
            f'<BODY><DESC><STATICVARIABLES>{_statics(company)}</STATICVARIABLES><TDL><TDLMESSAGE>{coll}</TDLMESSAGE></TDL></DESC></BODY></ENVELOPE>')


def post_tally(xml: str, port: int = DEFAULT_PORT, timeout: int = 60) -> bytes:
    if not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("invalid Tally port")
    with httpx.Client(trust_env=False, timeout=timeout) as c:      # trust_env=False: never route loopback through a proxy
        try:
            r = c.post(f"http://127.0.0.1:{port}", content=xml.encode("utf-8"), headers={"Content-Type": "text/xml; charset=utf-8"})
        except httpx.TransportError:
            raise ValueError(f"Tally is not reachable on 127.0.0.1:{port}. Open TallyPrime, enable its HTTP server and load the company.") from None
    return r.content[:MAX_RESPONSE]


def _root(raw: bytes) -> ET.Element:
    return ET.fromstring(BAD.sub("", raw.decode("utf-8", "replace")))     # ElementTree never fetches external entities


def parse_date(text: str) -> date | None:
    t = (text or "").strip()
    for fmt in ("%d-%b-%y", "%d-%b-%Y", "%Y%m%d", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(t, fmt).date()
        except ValueError:
            pass
    return None


def parse_bills(raw: bytes) -> list[dict]:
    """Tally's Bills Receivable / Payable report is a flat run of BILLFIXED, BILLCL, BILLDUE elements per bill."""
    bills, cur = [], None
    for el in _root(raw).iter():
        if el.tag == "BILLFIXED":
            cur = {"date": parse_date(el.findtext("BILLDATE") or ""), "ref": (el.findtext("BILLREF") or "").strip(),
                   "party": (el.findtext("BILLPARTY") or "").strip(), "amount": 0.0, "due": None}
            bills.append(cur)
        elif cur is not None and el.tag == "BILLCL":
            try:
                cur["amount"] = abs(float((el.text or "0").replace(",", "")))
            except ValueError:
                cur["amount"] = 0.0
        elif cur is not None and el.tag == "BILLDUE":
            cur["due"] = parse_date(el.text or "")
    return [b for b in bills if b["party"] and b["ref"] and b["amount"] > 0 and b["date"]]


def parse_parties(raw: bytes) -> dict[str, dict]:
    out = {}
    for led in _root(raw).iter("LEDGER"):
        name = (led.get("NAME") or led.findtext("NAME") or "").strip()
        if name:
            phone = re.sub(r"\D", "", led.findtext("LEDGERMOBILE") or led.findtext("LEDGERPHONE") or "")[-10:]
            out[name] = {"phone": phone, "gstin": (led.findtext("PARTYGSTIN") or "").strip() or None}
    return out


def build_payload(receivables: list[dict], payables: list[dict], parties: dict[str, dict],
                  msme: dict[str, str] | None = None, credit_days: int = 30) -> dict:
    """Receivables need a WhatsApp number; parties without one are listed as skipped so the owner can fix them in Tally."""
    msme = {k.lower(): v for k, v in (msme or {}).items()}
    recv, skipped = [], []
    for b in receivables:
        info = parties.get(b["party"], {})
        if len(info.get("phone", "")) < 10:
            skipped.append(b["party"])
            continue
        recv.append({"party": b["party"], "phone": info["phone"], "invoice_ref": b["ref"], "amount_rupees": b["amount"],
                     "due_date": (b["due"] or b["date"] + timedelta(days=credit_days)).isoformat(), "gstin": info.get("gstin")})
    pay = [{"supplier": b["party"], "bill_ref": b["ref"], "bill_date": b["date"].isoformat(), "amount_rupees": b["amount"],
            "gstin": parties.get(b["party"], {}).get("gstin"), "msme_class": msme.get(b["party"].lower(), "unknown")}
           for b in payables]
    return {"receivables": recv, "payables": pay, "_skipped_no_phone": sorted(set(skipped))}


def collect(port: int = DEFAULT_PORT, company: str = "", msme: dict[str, str] | None = None) -> dict:
    parties = parse_parties(post_tally(_ledger_request(company), port))
    recv = parse_bills(post_tally(_request("Bills Receivable", company), port))
    pay = parse_bills(post_tally(_request("Bills Payable", company), port))
    return build_payload(recv, pay, parties, msme)


def send(server: str, token: str, payload: dict) -> dict:
    body = {k: v for k, v in payload.items() if not k.startswith("_")}
    result: dict = {}
    for i in range(0, max(len(body["receivables"]), len(body["payables"]), 1), CHUNK):
        part = {"receivables": body["receivables"][i:i + CHUNK], "payables": body["payables"][i:i + CHUNK]}
        r = httpx.post(f"{server}/bridge/sync/tally", json=part, headers={"Authorization": f"Bearer {token}"}, timeout=120)
        r.raise_for_status()
        result = r.json()
    return result


def due_now(last_run: float | None, now: datetime) -> bool:
    """Once per local day, after RUN_AFTER_HOUR."""
    if now.hour < RUN_AFTER_HOUR:
        return False
    return last_run is None or datetime.fromtimestamp(last_run).date() < now.date()
