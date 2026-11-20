"""open_spreadsheet and edit_spreadsheet: the owner's Excel/CSV, looked at and changed by a program, not by hand-written code.

The engine (sheet_engine.py) runs inside the sandbox; its source travels with each job, so these tools work the same on
any sandbox image. Each edit saves a new file under the owner's own file name and returns its file_ref: the NEXT change
must start from that file, so edits build on each other instead of each starting again from the original.
"""
import json
import re
from pathlib import Path

from src.agent import actions
from src.box.sandbox import safe_name

ENGINE = (Path(__file__).with_name("sheet_engine.py")).read_bytes()
SHEET_EXT = (".xlsx", ".xlsm", ".csv", ".tsv", ".xls")
CODE = ("import sys\nsys.path.insert(0, 'in')\nimport _sheet_engine as se\nRESULT = se.main('in/_spec.json')\n")
RESULT_CHARS = 5800


def _trim(result: dict) -> dict:
    """Keep a layout inside one tool result: fewer preview rows first, then fewer columns described."""
    for _ in range(6):
        if len(json.dumps(result, ensure_ascii=False, default=str)) <= RESULT_CHARS:
            return result
        for s in result.get("sheets", []):
            for k in ("first_rows", "rows", "found"):
                if isinstance(s.get(k), list) and len(s[k]) > 2:
                    s[k] = s[k][: max(2, len(s[k]) // 2)]
                    s[f"{k}_cut"] = "shortened to fit; ask for fewer rows (rows='A-B') to see the rest"
            if len(s.get("columns", [])) > 12:
                s["columns_cut"] = f"{len(s['columns'])} columns; showing the first 12"
                s["columns"] = s["columns"][:12]
        if len(result.get("sheets", [])) > 3:
            result["sheets_cut"] = [s["sheet"] for s in result["sheets"][3:]]
            result["sheets"] = result["sheets"][:3]
    return result


async def _run(app, ctx, file_ref: str, spec: dict, timeout_s: int = 240) -> tuple[dict, str, list]:
    data, _, filename = await actions._fetch(app, file_ref, ctx)
    name = safe_name(filename or "file.xlsx")
    if not name.lower().endswith(SHEET_EXT):
        return {"error": f"{filename!r} is not a spreadsheet (xlsx, xlsm, csv). For other files use read_attachment."}, name, []
    spec = {**spec, "src": f"in/{name}"}
    if spec["op"] == "edit":
        spec["dst"] = f"out/{name}"                      # the owner gets their own file name back
    res = await app.state.sandbox.run(CODE, {name: data, "_sheet_engine.py": ENGINE,
                                             "_spec.json": json.dumps(spec, ensure_ascii=False).encode("utf-8")},
                                      timeout_s=timeout_s)
    if res.get("timed_out"):
        return {"error": "the file is too big to go through here in time. Use run_python with pandas for this one and say so."}, name, []
    out = res.get("result")
    if not isinstance(out, dict):
        err = (res.get("error") or res.get("stderr") or "the spreadsheet could not be read")[-600:]
        return {"error": err}, name, []
    refs = [f"sandbox:{res['job_id']}:{n}" for n in res.get("outputs") or []] if res.get("job_id") else []
    return out, name, refs


async def open_spreadsheet(app, ctx, args: dict) -> dict:
    ref = str(args.get("file_ref") or "")
    spec = {"op": "layout", **{k: args[k] for k in ("sheet", "rows", "find") if args.get(k)}}
    if spec.get("rows") and not re.fullmatch(r"\s*\d+\s*(-\s*\d+\s*)?", str(spec["rows"])):
        return {"error": "rows must look like '100-130' or '45'"}
    out, _, _ = await _run(app, ctx, ref, spec)
    if "error" not in out:
        out["how_to_read"] = ("header_row is the row of column titles; data_rows are the entries; total_rows and "
                              "rows_below_totals are NOT entries (never count them as people or add them into a total). "
                              "Quote sum_of_data_rows and the file's own total row instead of adding up numbers yourself.")
    return _trim(out)


async def edit_spreadsheet(app, ctx, args: dict) -> dict:
    ref = str(args.get("file_ref") or "")
    changes = args.get("changes")
    if isinstance(changes, dict):
        changes = [changes]
    if not isinstance(changes, list) or not changes or not all(isinstance(c, dict) for c in changes):
        return {"error": "changes must be a list like [{\"column\": \"Aug Collection\", \"value\": \"Paid\"}]"}
    if len(changes) > 20:
        return {"error": "at most 20 changes in one call"}
    out, name, refs = await _run(app, ctx, ref, {"op": "edit", "changes": changes, "preview": bool(args.get("preview"))})
    if out.get("saved") and out.get("verified") and refs:
        out["file_ref"] = refs[0]
        out["filename"] = name
        out["_new_files"] = refs
        out["next"] = ("Send it with send_file_to_owner(file_ref). Tell the owner in plain words: which column, how many rows, "
                       "and the effects listed here (formulas that now show #VALUE!, totals that moved) and what was left "
                       "alone (total row, rows below it). For ANY further change to this file, use THIS file_ref, not the "
                       "original, so this change is kept.")
    elif out.get("saved") and not out.get("verified"):
        out = {**{k: v for k, v in out.items() if k not in ("saved",)}, "saved": False,
               "error": "the saved copy did not match the plan; it was NOT kept. Tell the owner it could not be done safely."}
    return out
