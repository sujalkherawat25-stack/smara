"""Live Excel control through its object model (COM), not UI clicking.

Bounded: workspace-only paths, macros force-disabled, no external-data formulas,
writes stay in memory until excel_save (which needs native approval).
"""
from __future__ import annotations
from pathlib import Path
import re

MAX_CELLS = 2000
BAD_FORMULA = re.compile(r"\b(WEBSERVICE|FILTERXML|HYPERLINK|CALL|REGISTER|REGISTER\.ID|EXEC|RTD|DDE|SHELL|ENCODEURL)\s*\(|https?://|file:|\\\\", re.I)
ERRORS = {-2146826281: "#DIV/0!", -2146826246: "#N/A", -2146826259: "#NAME?", -2146826288: "#NULL!", -2146826252: "#NUM!", -2146826265: "#REF!", -2146826273: "#VALUE!"}
ERROR_TEXT = {"#DIV/0!", "#N/A", "#NAME?", "#NULL!", "#NUM!", "#REF!", "#VALUE!", "#SPILL!", "#CALC!"}
ADDRESS = re.compile(r"\$?[A-Za-z]{1,3}\$?\d{1,7}(:\$?[A-Za-z]{1,3}\$?\d{1,7})?")


def _app(create=True):
    import pythoncom
    import win32com.client as client
    pythoncom.CoInitialize()
    try: return client.GetActiveObject("Excel.Application")
    except Exception:
        if not create: return None
        app = client.DispatchEx("Excel.Application"); app.Visible = True
        return app


def _inside(workspace, path):
    base = Path(workspace).resolve()
    resolved = Path(path).resolve() if Path(path).is_absolute() else (base / path).resolve()
    if not resolved.is_relative_to(base): raise ValueError("Excel paths must stay inside the project folder")
    return resolved


def _workbook(app, name):
    if name:
        for book in app.Workbooks:
            if str(name).lower() in (book.Name.lower(), book.FullName.lower()): return book
        raise ValueError("Workbook is not open: " + str(name))
    if app.Workbooks.Count == 0: raise ValueError("No workbook is open")
    return app.ActiveWorkbook


def _sheet(book, name):
    if not name: return book.ActiveSheet
    try: return book.Worksheets(name)
    except Exception: raise ValueError("Unknown sheet: " + str(name)) from None


def _cells(sheet, address):
    if not isinstance(address, str) or not ADDRESS.fullmatch(address.strip()): raise ValueError("Range must look like A1 or A1:C20")
    rng = sheet.Range(address.strip())
    if rng.Count > MAX_CELLS: raise ValueError(f"Range exceeds {MAX_CELLS} cells")
    return rng


def _plain(value):
    if isinstance(value, int) and value in ERRORS: return ERRORS[value]
    if hasattr(value, "isoformat"): return value.isoformat()
    return value


def _grid(rng, attr):
    raw = getattr(rng, attr)
    if rng.Count == 1: return [[_plain(raw)]]
    return [[_plain(v) for v in row] for row in raw]


def call(name, args, workspace, permitted=lambda: True):
    if name == "excel_save": return save(args, workspace)
    action = args.get("action")
    if action == "list":
        app = _app(create=False)
        if app is None: return {"running": False, "workbooks": []}
        return {"running": True, "workbooks": [{"name": b.Name, "path": b.FullName, "read_only": bool(b.ReadOnly), "saved": bool(b.Saved),
                                                 "sheets": [s.Name for s in b.Worksheets]} for b in app.Workbooks]}
    if action == "open":
        path = _inside(workspace, args.get("path", ""))
        if not path.is_file() or path.suffix.lower() not in {".xlsx", ".xlsm", ".xls", ".csv"}: raise ValueError("Open a workbook file inside the project")
        app = _app()
        app.AutomationSecurity = 3  # msoAutomationSecurityForceDisable: no macros on open
        app.DisplayAlerts = False
        book = app.Workbooks.Open(str(path), UpdateLinks=0, ReadOnly=bool(args.get("read_only", False)), AddToMru=False)
        return {"opened": book.Name, "sheets": [s.Name for s in book.Worksheets]}
    app = _app(create=False)
    if app is None: raise ValueError("Excel is not running; use excel_live open first")
    book = _workbook(app, args.get("workbook"))
    sheet = _sheet(book, args.get("sheet"))
    if action == "read":
        rng = _cells(sheet, args.get("range", ""))
        return {"workbook": book.Name, "sheet": sheet.Name, "range": rng.Address.replace("$", ""), "values": _grid(rng, "Value2"), "formulas": _grid(rng, "Formula")}
    if action == "write":
        if book.ReadOnly: raise ValueError("Workbook is read-only")
        rng = _cells(sheet, args.get("range", ""))
        values = args.get("values")
        if not isinstance(values, list) or not values or not all(isinstance(r, list) for r in values): raise ValueError("values must be a 2-D array")
        rows, cols = len(values), max(len(r) for r in values)
        if rows * cols > MAX_CELLS: raise ValueError("Too many cells")
        for row in values:
            for cell in row:
                if isinstance(cell, str) and cell.startswith("=") and BAD_FORMULA.search(cell): raise ValueError("Formula uses an external-data or code-execution function; not allowed")
        top = rng.Cells(1, 1)
        for r, row in enumerate(values):
            for c, cell in enumerate(row):
                if not permitted(): raise ValueError("Computer control revoked or expired")
                target = top.Offset(r + 1, c + 1)
                if isinstance(cell, str) and cell.startswith("="): target.Formula = cell
                else: target.Value2 = cell
        written = sheet.Range(top, top.Offset(rows, cols))
        return {"written": written.Address.replace("$", ""), "saved": False, "values": _grid(written, "Value2")}
    if action == "formula_check":
        rng = _cells(sheet, args.get("range", ""))
        app.Calculate()
        values, formulas = _grid(rng, "Value2"), _grid(rng, "Formula")
        errors = [{"row": r, "col": c, "value": v} for r, row in enumerate(values) for c, v in enumerate(row) if isinstance(v, str) and v in ERROR_TEXT]
        mismatches = []
        for address, expected in (args.get("expect") or {}).items():
            actual = _plain(_cells(sheet, address).Value2)
            numeric = isinstance(actual, (int, float)) and isinstance(expected, (int, float))
            if not (abs(actual - expected) < 1e-9 if numeric else actual == expected): mismatches.append({"cell": address, "expected": expected, "actual": actual})
        return {"range": rng.Address.replace("$", ""), "values": values, "formulas": formulas, "errors": errors, "mismatches": mismatches, "ok": not errors and not mismatches}
    raise ValueError("Unsupported excel_live action")


def save(args, workspace):
    app = _app(create=False)
    if app is None: raise ValueError("Excel is not running")
    book = _workbook(app, args.get("workbook"))
    if args.get("path"):
        path = _inside(workspace, args["path"])
        if path.suffix.lower() != ".xlsx": raise ValueError("Save as .xlsx")
        book.SaveAs(str(path), FileFormat=51)
    else:
        _inside(workspace, book.FullName)  # only files inside the project can be overwritten
        book.Save()
    return {"saved": book.FullName}
