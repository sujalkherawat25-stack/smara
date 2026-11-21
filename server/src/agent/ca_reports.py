"""Finished answers for accountants' work: the tools in gst.py, bank.py and ageing.py compute everything, make the PDF and a full Excel,
send both to the owner, and hand the model a few plain sentences to quote. A model that has to assemble the answer from raw figures
sometimes recomputes it in its own code and gets it wrong (seen: a correct reconciliation ignored, then redone with 17 invoices
missing instead of 14), so the figures arrive already worded and the files arrive already sent.
"""
import json

# Runs in the sandbox: one sheet per entry of {"sheets": [[title, header, rows]]}; a name that begins with = + - @ is kept as text.
XL_CODE = """
import json
from openpyxl import Workbook
from openpyxl.styles import Font
d = json.load(open('in/_data.json', encoding='utf-8'))
wb = Workbook(); wb.remove(wb.active)
for title, header, rows in d['sheets']:
    ws = wb.create_sheet(title[:31]); ws.append(header)
    for r in rows:
        ws.append(r)
        for c in ws[ws.max_row]:
            if isinstance(c.value, str) and c.value[:1] in '=+-@':
                c.data_type = 's'
    for c in ws[1]:
        c.font = Font(bold=True)
    ws.freeze_panes = 'A2'
    for col in ws.columns:
        ws.column_dimensions[col[0].column_letter].width = min(42, max(10, max(len(str(c.value or '')) for c in col[:300]) + 2))
wb.save('out/' + d['name'])
RESULT = {'name': d['name']}
"""


async def excel_file(app, name: str, sheets: list):
    """(file_ref, name) of a workbook made in the sandbox from [(title, header, rows)], or None (the PDF still goes out)."""
    try:
        out = await app.state.sandbox.run(XL_CODE, {"_data.json": json.dumps({"name": name, "sheets": sheets}, ensure_ascii=False, default=str).encode("utf-8")},
                                          timeout_s=120)
    except Exception:
        return None
    if out.get("job_id") and name in (out.get("outputs") or []):
        return f"sandbox:{out['job_id']}:{name}", name
    return None


async def deliver(app, ctx, *, title: str, subtitle: str, blocks: list, xl_name: str, sheets: list, caption: str, send: bool = True) -> dict:
    """Make the PDF and the Excel and (unless send is False) send both to the owner. Returns what was made and what was sent:
    {"files": [...names], "sent": [...names], "not_sent": {name: reason}, "file_refs": {...}, "_new_files": [...]}."""
    from src.agent import documents, extras
    made: dict[str, str] = {}
    doc = await documents.make_document(app, ctx, {"kind": "custom", "title": title, "subtitle": subtitle, "blocks": blocks})
    if "file_ref" in doc:
        made[doc["filename"]] = doc["file_ref"]
    xl = await excel_file(app, xl_name, sheets)
    if xl:
        made[xl[1]] = xl[0]
    out = {"files": list(made), "file_refs": made, "_new_files": list(made.values()), "sent": [], "not_sent": {}}
    if "file_ref" not in doc:
        out["not_sent"]["report.pdf"] = doc.get("error", "the PDF could not be made")
    if not xl:
        out["not_sent"][xl_name] = "the Excel could not be made"
    if send:
        for name, ref in made.items():
            r = await extras.send_file_to_owner(app, ctx, {"file_ref": ref, "caption": caption if name == xl_name else f"{caption} (PDF)"})
            (out["sent"].append(name) if r.get("sent") else out["not_sent"].__setitem__(name, r.get("error", "not sent")))
    return out


def rs(x: float) -> str:
    """Indian digit grouping, two decimals: 7286766.86 -> 72,86,766.86."""
    neg, x = x < 0, abs(round(x, 2))
    whole, frac = f"{x:.2f}".split(".")
    head, tail = whole[:-3], whole[-3:]
    while len(head) > 2:
        tail, head = head[-2:] + "," + tail, head[:-2]
    return ("-" if neg else "") + "Rs " + (head + "," if head else "") + tail + "." + frac


RESULT_LIMIT = 5200      # tools.bounded() cuts a tool result at 6000 characters: whatever sits past that is never seen by the model


def compact(res: dict, first: tuple, cap: int = 5) -> dict:
    """The tool result in an order and size the model can use: the sentences to quote and the instruction first, then the headline figures,
    then only a few rows of each list (the Excel holds every row). Anything that would still not fit is cut from the lists, never from the
    front. Keys that start with _ (the files the session may open) are kept as they are."""
    out = {k: res[k] for k in first if k in res}
    for k, v in res.items():
        if k not in out:
            out[k] = v[:cap] if isinstance(v, list) and not k.startswith("_") else v
    for k in sorted((k for k, v in out.items() if isinstance(v, list) and not k.startswith("_") and k not in first), key=lambda k: -len(json.dumps(out[k], default=str))):
        if len(json.dumps(out, default=str, ensure_ascii=False)) <= RESULT_LIMIT:
            break
        out[k] = out[k][:2]
        out[f"{k}_cut"] = "first 2 only; the Excel has every row"
    return out
