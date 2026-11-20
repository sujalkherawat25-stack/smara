"""Look at an owner's spreadsheet and change it exactly as asked, nothing more.

Seen on 20 Nov 2026 (an electricity-bill register of a girls' hostel): asked to "write paid in the Aug Collection column",
the agent zeroed another column, overwrote the Total row's SUM formula, wrote into the extra block below the totals,
started each follow-up edit from the original file (so the previous change silently vanished) and said "nothing else
changed" while its own compare said otherwise. Writing openpyxl code by hand for every edit is where that comes from.

This module does the edit deterministically from a small spec (column by its header, which rows, what value), never
touches a column or row that was not named, works out what the change does to the formulas that use it, and checks the
saved file cell by cell against the original before saying it is done. It runs inside the sandbox (stdlib + openpyxl
only): the tool sends this file along with the job, so it does not depend on the sandbox image being rebuilt.
"""
import csv
import io
import json
import re
import zipfile
from pathlib import Path

TOTAL_RE = re.compile(r"^\s*(grand\s*total|sub\s*-?\s*total|net\s*total|total|totals|kul|jod|योग|कुल)\b", re.I)
REF_RE = re.compile(r"(?<![A-Za-z_!])\$?([A-Z]{1,3})\$?(\d{1,7})(?::\$?([A-Z]{1,3})\$?(\d{1,7}))?(?![A-Za-z_(])")
LOSSY_PARTS = (("xl/charts/", "charts"), ("xl/drawings/", "pictures or shapes"), ("xl/pivotTables/", "pivot tables"),
               ("xl/media/", "images"), ("xl/externalLinks/", "links to other files"), ("xl/slicers/", "slicers"))
BIG_ROWS = 60_000          # above this, layout is read in streaming mode and edits are refused (too slow and memory-hungry)
ERR = "#VALUE!"


class _Unknown:
    """A formula this module cannot work out (a function it does not know, another sheet). Never a guess."""
    def __repr__(self):
        return "unknown"


UNK = _Unknown()


# ---------------------------------------------------------------- small helpers
def col_index(letters: str) -> int:
    n = 0
    for ch in letters.upper():
        n = n * 26 + (ord(ch) - 64)
    return n


def col_letter(n: int) -> str:
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def norm(s) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(s or "").casefold())


def is_formula(v) -> bool:
    return isinstance(v, str) and v.startswith("=")


def is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def short(v, n: int = 40):
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    s = v if not isinstance(v, str) else (v if len(v) <= n else v[:n] + "…")
    return s


def as_number(v):
    """A cell value as a number for arithmetic: blank is 0, '1,250' is 1250, other text is an error (like Excel)."""
    if v is None or v == "":
        return 0
    if isinstance(v, bool):
        return int(v)
    if is_num(v):
        return v
    if isinstance(v, str):
        t = v.strip().replace(",", "").replace("₹", "")
        try:
            return float(t)
        except ValueError:
            return ERR
    return ERR


# ---------------------------------------------------------------- a small formula evaluator (enough for registers)
class _Eval:
    """Evaluates the simple formulas registers use: + - * / ^, brackets, cell refs and SUM/MIN/MAX/AVERAGE/COUNT/ROUND/ABS.
    Anything else gives None (unknown), never a wrong number. Files saved by openpyxl have no cached values, so follow-up
    edits must be able to work out what a formula column shows."""

    def __init__(self, cells: dict, cached: dict | None = None):
        self.cells, self.cached, self.memo, self.busy = cells, cached or {}, {}, set()

    def value(self, r: int, c: int):
        key = (r, c)
        if key in self.memo:
            return self.memo[key]
        v = self.cells.get(key)
        if is_formula(v):
            if key in self.busy:
                return UNK
            self.busy.add(key)
            saved = (getattr(self, "toks", None), getattr(self, "i", 0))    # a formula inside a formula: keep the outer place
            try:
                out = self._formula(v[1:])
            except Exception:
                out = UNK
            self.toks, self.i = saved
            self.busy.discard(key)
            if out is UNK and self.cached.get(key) is not None:
                out = self.cached[key]
        else:
            out = v
        self.memo[key] = out
        return out

    def _formula(self, text: str):
        self.toks = re.findall(r"\$?[A-Z]{1,3}\$?\d+(?::\$?[A-Z]{1,3}\$?\d+)?|\d+\.?\d*(?:[eE][+-]?\d+)?|[A-Za-z_]+(?=\()|"
                               r"\"[^\"]*\"|<>|<=|>=|[-+*/^(),:<>=&%]", text.replace(" ", ""))
        if "".join(self.toks) != text.replace(" ", ""):
            return UNK
        self.i = 0
        out = self._expr()
        return out if self.i == len(self.toks) else UNK

    def _peek(self):
        return self.toks[self.i] if self.i < len(self.toks) else None

    def _expr(self):
        v = self._sum()
        op = self._peek()
        if op in ("<", ">", "=", "<=", ">=", "<>"):
            self.i += 1
            w = self._sum()
            if UNK in (v, w):
                return UNK
            if op in ("=", "<>"):
                same = (as_number(v) == as_number(w)) if ERR not in (as_number(v), as_number(w)) else norm(v) == norm(w)
                return same if op == "=" else not same
            x, y = as_number(v), as_number(w)
            if ERR in (x, y):
                return ERR
            return {"<": x < y, ">": x > y, "<=": x <= y, ">=": x >= y}[op]
        return v

    def _sum(self):
        v = self._term()
        while self._peek() in ("+", "-"):
            op = self.toks[self.i]
            self.i += 1
            w = self._term()
            v = self._arith(v, w, op)
        return v

    def _term(self):
        v = self._power()
        while self._peek() in ("*", "/"):
            op = self.toks[self.i]
            self.i += 1
            w = self._power()
            v = self._arith(v, w, op)
        return v

    def _power(self):
        v = self._unary()
        while self._peek() == "^":
            self.i += 1
            v = self._arith(v, self._unary(), "^")
        return v

    def _unary(self):
        if self._peek() == "-":
            self.i += 1
            v = self._unary()
            if v is UNK:
                return UNK
            n = as_number(v)
            return ERR if n == ERR else -n
        if self._peek() == "+":
            self.i += 1
            return self._unary()
        return self._atom()

    def _arith(self, a, b, op):
        if a is UNK or b is UNK:
            return UNK
        x, y = as_number(a), as_number(b)
        if ERR in (x, y):
            return ERR
        if op == "+":
            return x + y
        if op == "-":
            return x - y
        if op == "*":
            return x * y
        if op == "/":
            return ERR if y == 0 else x / y
        return x ** y

    def _range_values(self, tok):
        m = REF_RE.fullmatch(tok)
        c1, r1 = col_index(m.group(1)), int(m.group(2))
        c2, r2 = (col_index(m.group(3)), int(m.group(4))) if m.group(3) else (c1, r1)
        if (r2 - r1 + 1) * (c2 - c1 + 1) > 500_000:
            return UNK
        return [self.value(r, c) for r in range(min(r1, r2), max(r1, r2) + 1) for c in range(min(c1, c2), max(c1, c2) + 1)]

    def _atom(self):
        t = self._peek()
        if t is None:
            raise ValueError("end")
        self.i += 1
        if t == "(":
            v = self._expr()
            if self._peek() != ")":
                raise ValueError("bracket")
            self.i += 1
            return v
        if re.fullmatch(r"\d+\.?\d*(?:[eE][+-]?\d+)?", t):
            f = float(t)
            return int(f) if f.is_integer() else f
        if t.startswith('"'):
            return t[1:-1]
        if REF_RE.fullmatch(t):
            if ":" in t:
                raise ValueError("range outside a function")
            m = REF_RE.fullmatch(t)
            return self.value(int(m.group(2)), col_index(m.group(1)))
        name = t.upper()
        if self._peek() != "(":
            raise ValueError("unknown")
        self.i += 1
        args = []
        while self._peek() != ")":
            tok = self._peek()
            if tok is not None and REF_RE.fullmatch(tok) and ":" in tok:
                self.i += 1
                vals = self._range_values(tok)
                if vals is UNK:
                    return UNK
                args.append(("range", vals))
            else:
                args.append(("one", self._expr()))
            if self._peek() == ",":
                self.i += 1
            elif self._peek() != ")":
                raise ValueError("args")
        self.i += 1
        return self._call(name, args)

    def _call(self, name, args):
        if name in ("SUM", "MIN", "MAX", "AVERAGE", "COUNT"):
            nums = []
            for kind, v in args:
                for x in (v if kind == "range" else [v]):
                    if x is UNK:
                        return UNK
                    if x == ERR:
                        return ERR
                    if is_num(x):
                        nums.append(x)
                    elif kind == "one" and x not in (None, ""):
                        n = as_number(x)
                        if n == ERR:
                            return ERR
                        nums.append(n)
            if name == "SUM":
                return sum(nums)
            if name == "COUNT":
                return len(nums)
            if not nums:
                return 0 if name != "AVERAGE" else ERR
            return min(nums) if name == "MIN" else max(nums) if name == "MAX" else sum(nums) / len(nums)
        if name == "IF" and 2 <= len(args) <= 3 and all(k == "one" for k, _ in args):
            cond = args[0][1]
            if cond is UNK or cond == ERR:
                return cond
            pick = args[1][1] if (cond if isinstance(cond, bool) else as_number(cond) not in (0, ERR)) else \
                (args[2][1] if len(args) == 3 else False)
            return pick
        if name in ("ROUND", "ABS") and args and args[0][0] == "one":
            if args[0][1] is UNK:
                return UNK
            x = as_number(args[0][1])
            if x == ERR:
                return ERR
            if name == "ABS":
                return abs(x)
            d = as_number(args[1][1]) if len(args) > 1 else 0
            return round(x, int(d)) if d != ERR else ERR
        return UNK


def refs_in(formula: str) -> list[tuple[int, int, int, int]]:
    """(row1, col1, row2, col2) for every cell or range a formula reads."""
    out = []
    for m in REF_RE.finditer(formula.replace(" ", "")):
        c1, r1 = col_index(m.group(1)), int(m.group(2))
        c2, r2 = (col_index(m.group(3)), int(m.group(4))) if m.group(3) else (c1, r1)
        out.append((min(r1, r2), min(c1, c2), max(r1, r2), max(c1, c2)))
    return out


# ---------------------------------------------------------------- reading a sheet's shape
class Sheet:
    """One sheet's cells (formulas as written) plus what the file last showed for them, and its shape: where the header is,
    which rows are the data, which rows are totals, and what sits below the totals."""

    def __init__(self, title: str, cells: dict, cached: dict, max_row: int, max_col: int):
        self.title, self.cells, self.cached, self.max_row, self.max_col = title, cells, cached, max_row, max_col
        self.ev = _Eval(cells, cached)
        self._shape()

    # -- shape
    def row_vals(self, r: int) -> list:
        return [self.cells.get((r, c)) for c in range(1, self.max_col + 1)]

    def blank(self, r: int) -> bool:
        return all(v in (None, "") for v in self.row_vals(r))

    def _label(self, r: int) -> str:
        for v in self.row_vals(r)[:6]:
            if isinstance(v, str) and v.strip() and not is_formula(v):
                return v.strip()
        return ""

    def is_total_row(self, r: int) -> bool:
        if TOTAL_RE.match(self._label(r)):
            return True
        sums = 0
        for v in self.row_vals(r):
            if is_formula(v) and re.search(r"\bSUM\(", v, re.I):
                if any(r2 - r1 >= 2 for r1, _, r2, _ in refs_in(v)):
                    sums += 1
        return sums >= 2

    def _shape(self):
        best, hdr = 0, 1
        for r in range(1, min(self.max_row, 30) + 1):
            texts = sum(1 for v in self.row_vals(r) if isinstance(v, str) and v.strip() and not is_formula(v))
            if texts > best and texts >= 2:
                best, hdr = texts, r
        self.header_row = hdr
        self.headers = {c: (str(self.cells.get((hdr, c))).strip() if self.cells.get((hdr, c)) not in (None, "") else "")
                        for c in range(1, self.max_col + 1)}
        start, end, totals, r, gap = hdr + 1, hdr, [], hdr + 1, 0
        last = max((rr for (rr, _), v in self.cells.items() if v not in (None, "")), default=hdr)
        while r <= last:
            if self.is_total_row(r):
                totals.append(r)
                break
            if self.blank(r):
                gap += 1
                if gap >= 2 and any(not self.blank(x) for x in range(r + 1, last + 1)):
                    break
            else:
                gap, end = 0, r
            r += 1
        self.data_start, self.data_end = start, max(end, hdr)
        r = (totals[0] if totals else self.data_end) + 1
        while r <= last and (self.is_total_row(r) or self.blank(r)):   # a second total line (Grand total) right after
            if self.is_total_row(r):
                totals.append(r)
            r += 1
        self.total_rows = totals
        self.below = [rr for rr in range(r, last + 1) if not self.blank(rr)] if (totals or self.data_end < last) else []
        if not totals and self.below and self.below[0] <= self.data_end:
            self.below = []
        self.last_row = last

    def data_rows(self) -> list[int]:
        return [r for r in range(self.data_start, self.data_end + 1) if not self.blank(r)]

    def has_content(self, r: int, skip_cols=()) -> bool:
        """A real entry: some typed value other than formulas (a formula-only row is an empty template line)."""
        return any(v not in (None, "") and v != 0 and not is_formula(v)
                   for c, v in enumerate(self.row_vals(r), 1) if c not in skip_cols)

    # -- columns
    def find_column(self, name) -> tuple[int | None, str]:
        """A column by its header (any case, spaces or punctuation), or by its letter. Returns (index, error)."""
        raw = str(name or "").strip()
        if not raw:
            return None, "say which column (its header as it appears in the sheet)"
        key = norm(raw)
        exact = [c for c, h in self.headers.items() if h and norm(h) == key]
        if len(exact) == 1:
            return exact[0], ""
        if re.fullmatch(r"[A-Za-z]{1,3}", raw) and not exact and col_index(raw) <= max(self.max_col, 1) + 1:
            return col_index(raw), ""
        part = [c for c, h in self.headers.items() if h and key and (key in norm(h) or norm(h) in key)]
        if len(exact) > 1 or len(part) > 1:
            cands = [f"{col_letter(c)} '{self.headers[c]}'" for c in (exact or part)]
            return None, f"'{raw}' fits more than one column: {', '.join(cands)}. Ask the owner which one."
        if len(part) == 1:
            return part[0], ""
        return None, (f"no column called '{raw}' in sheet '{self.title}'. Columns: "
                      + ", ".join(f"{col_letter(c)} '{h}'" for c, h in self.headers.items() if h))

    def used_by(self, col: int) -> list[str]:
        """Columns whose formulas read this column (e.g. G 'Aug Total' reads F)."""
        users = {}
        for (r, c), v in self.cells.items():
            if c != col and is_formula(v) and r not in self.total_rows:
                if any(c1 <= col <= c2 for _, c1, _, c2 in refs_in(v)):
                    users.setdefault(c, v)
        return [f"{col_letter(c)} '{self.headers.get(c, '')}' ({f})" for c, f in sorted(users.items())]

    def in_words(self, formula: str) -> str:
        """'=E2+F2-D2' as '[Aug-26 Paid Arpita] + [Aug Collection] - [Aug-26 Bill Amount]' (same-row cells only), so what a
        negative or positive result means can be read off instead of guessed."""
        refs = list(REF_RE.finditer(formula))
        if not refs or any(m.group(3) for m in refs):
            return ""
        rows = {int(m.group(2)) for m in refs}
        if len(rows) != 1:
            return ""
        spaced = re.sub(r"\s*([-+*/])\s*", r" \1 ", formula.lstrip("=")).strip()
        return REF_RE.sub(lambda m: f"[{self.headers.get(col_index(m.group(1))) or m.group(1)}]", spaced)

    def describe_column(self, c: int) -> dict:
        vals = [self.cells.get((r, c)) for r in self.data_rows()]
        nums = [v for v in vals if is_num(v)]
        forms = [v for v in vals if is_formula(v)]
        texts = [v for v in vals if isinstance(v, str) and v.strip() and not is_formula(v)]
        out = {"col": col_letter(c), "header": self.headers.get(c, "")}
        out["filled_rows"] = len([v for v in vals if v not in (None, "")])
        if nums:
            out["numbers"] = len(nums)
        if texts:
            out["text"] = len(texts)
            out["text_examples"] = sorted({short(t, 20) for t in texts})[:4]
        if forms:
            out["formulas"] = len(forms)
            out["formula_example"] = forms[0]
            if words := self.in_words(forms[0]):
                out["means"] = words
        shown = [self.ev.value(r, c) for r in self.data_rows()]
        if any(is_num(v) for v in shown) and not id_like(self.headers.get(c, "")):
            out["sum_of_data_rows"] = round(sum(v for v in shown if is_num(v)), 2)
        errs = sum(1 for v in shown if v == ERR)
        if errs:
            out["cells_showing_errors"] = errs
        if users := self.used_by(c):
            out["used_by_formulas_in"] = users[:3]
        return out

    def describe_row(self, r: int) -> dict:
        row = {}
        for c in range(1, self.max_col + 1):
            v = self.cells.get((r, c))
            if v in (None, ""):
                continue
            key = f"{col_letter(c)} {self.headers.get(c, '')}".strip()
            if is_formula(v):
                shown = self.ev.value(r, c)
                row[key] = f"{v} -> {short(shown)}" if shown is not UNK and shown is not None else v
            else:
                row[key] = short(v)
        return {"row": r, **row}


def _cells_of(ws) -> tuple[dict, int, int]:
    cells = {}
    for row in ws.iter_rows():
        for c in row:
            if c.value is not None and hasattr(c, "column"):
                cells[(c.row, c.column)] = c.value
    mr = max((r for r, _ in cells), default=0)
    mc = max((c for _, c in cells), default=0)
    return cells, mr, mc


def _read_csv(path: str) -> tuple[list, str]:
    raw = Path(path).read_bytes()
    text = raw.decode("utf-8-sig", "replace")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
        delim = dialect.delimiter
    except csv.Error:
        delim = ","
    rows = list(csv.reader(io.StringIO(text), delimiter=delim))
    return rows, delim


def _csv_cell(v: str):
    t = v.strip()
    if t == "":
        return None
    if re.fullmatch(r"-?\d+", t) and not (len(t) > 1 and t.startswith("0")) and len(t) < 16:
        return int(t)
    if re.fullmatch(r"-?\d+\.\d+", t):
        return float(t)
    return v


def lossy_parts(path: str) -> list[str]:
    try:
        names = zipfile.ZipFile(path).namelist()
    except zipfile.BadZipFile:
        return []
    return sorted({label for n in names for prefix, label in LOSSY_PARTS if n.startswith(prefix)})


def load(path: str, big_ok: bool = True) -> dict:
    """{'kind', 'sheets': [Sheet], 'notes': [...]} for .xlsx/.xlsm/.csv."""
    low = path.lower()
    if low.endswith(".csv") or low.endswith(".tsv") or low.endswith(".txt"):
        rows, delim = _read_csv(path)
        cells = {(r, c): _csv_cell(v) for r, row in enumerate(rows, 1) for c, v in enumerate(row, 1) if v.strip() != ""}
        mc = max((len(r) for r in rows), default=0)
        return {"kind": "csv", "delimiter": delim, "sheets": [Sheet("csv", cells, {}, len(rows), mc)], "notes": []}
    if low.endswith(".xls"):
        raise ValueError("this is an old .xls file. It cannot be changed in place; tell the owner, and offer to save the "
                         "change as a new .xlsx (formatting may differ) if they want")
    from openpyxl import load_workbook
    keep_vba = low.endswith(".xlsm")
    probe = load_workbook(path, read_only=True)
    sizes = {ws.title: (ws.max_row or 0) for ws in probe.worksheets}
    probe.close()
    big = max(sizes.values(), default=0) > BIG_ROWS
    if big and not big_ok:
        raise ValueError(f"the sheet has {max(sizes.values()):,} rows; editing cell by cell at that size is too slow here. "
                         "Use run_python with pandas for this file and send the result as a new file")
    wb = load_workbook(path, read_only=big, keep_vba=keep_vba)
    vals = load_workbook(path, read_only=True, data_only=True)
    sheets, notes = [], []
    for ws in wb.worksheets:
        if getattr(ws, "sheet_state", "visible") != "visible":
            notes.append(f"sheet '{ws.title}' is hidden")
        cells, mr, mc = _cells_of(ws)
        cached = {}
        if not big:
            for row in vals[ws.title].iter_rows():
                for c in row:
                    if hasattr(c, "column") and is_formula(cells.get((c.row, c.column))) and c.value is not None:
                        cached[(c.row, c.column)] = c.value
        sheets.append(Sheet(ws.title, cells, cached, mr, mc))
    vals.close()
    if big:
        wb.close()
    return {"kind": "xlsm" if keep_vba else "xlsx", "sheets": sheets, "notes": notes, "big": big, "rows": sizes}


_ID_WORDS = re.compile(r"(phone|mobile|pincode|gstin|account|room|year|code)")


def id_like(header) -> bool:
    """InvoiceNo, CustomerID, Room no., Mobile: numbers that are labels, never added up."""
    k = norm(header)
    return bool(k) and (k.endswith(("no", "id", "number", "num", "code")) or bool(_ID_WORDS.search(k))
                        or k in ("pin", "pan", "sno", "srno"))


def _stat_col(st: dict, v) -> None:
    if v in (None, ""):
        return
    st["filled_rows"] = st.get("filled_rows", 0) + 1
    if is_formula(v):
        st["formulas"] = st.get("formulas", 0) + 1
        st.setdefault("formula_example", v)
    elif is_num(v):
        st["numbers"] = st.get("numbers", 0) + 1
        st["_sum"] = st.get("_sum", 0) + v
        st["min"] = v if "min" not in st else min(st["min"], v)
        st["max"] = v if "max" not in st else max(st["max"], v)
    elif isinstance(v, str):
        st["text"] = st.get("text", 0) + 1
        ex = st.setdefault("text_examples", [])
        if len(ex) < 4 and short(v, 20) not in ex:
            ex.append(short(v, 20))
    else:                                        # dates and times
        st["dates"] = st.get("dates", 0) + 1
        st["first_date"] = str(v) if "first_date" not in st else min(st["first_date"], str(v))
        st["last_date"] = str(v) if "last_date" not in st else max(st["last_date"], str(v))


def _layout_big(path: str, sheet, rows, find, preview_rows: int) -> dict:
    """A very large sheet, read once as a stream: per-column counts and sums, first rows, a total row near the end, and
    any rows asked for. Nothing else is kept in memory, so 500,000 rows fit in the sandbox."""
    from collections import deque
    from openpyxl import load_workbook
    wb = load_workbook(path, read_only=True)
    out = {"file": Path(path).name, "sheets": [], "big": True,
           "note": "a big file: summarised in one pass. For grouping, top-N or charts use run_python with pandas "
                   "(timeout_s up to 300); edits cell by cell are not available at this size"}
    win = None
    if rows:
        a, _, b = str(rows).partition("-")
        win = (int(a), int(b or a))
    key = norm(find) if find else ""
    for ws in wb.worksheets:
        if sheet and norm(ws.title) != norm(sheet):
            continue
        head, hdr, best = [], 1, 0
        stats, tail, picked, hits, n_rows, first, fast = {}, deque(maxlen=40), [], [], 0, [], {}
        for r, vals in enumerate(ws.iter_rows(values_only=True), 1):
            if r <= 30:
                head.append(vals)
                texts = sum(1 for v in vals if isinstance(v, str) and v.strip() and not is_formula(v))
                if texts > best and texts >= 2:
                    best, hdr = texts, r
            if all(v in (None, "") for v in vals):
                continue
            n_rows = r
            tail.append((r, vals))
            if win and win[0] <= r <= win[1] and len(picked) < 60:
                picked.append((r, vals))
            if key and len(hits) < 25 and any(isinstance(v, str) and key in norm(v) for v in vals):
                hits.append((r, vals))
            if r > 30:
                for c, v in enumerate(vals, 1):
                    if v is None:
                        continue
                    t = type(v)
                    if t is int or t is float:                      # the common case, kept short for speed
                        st = fast.get(c)
                        if st is None:
                            st = fast[c] = [0, 0, v, v]
                        st[0] += 1
                        st[1] += v
                        if v < st[2]:
                            st[2] = v
                        elif v > st[3]:
                            st[3] = v
                    else:
                        _stat_col(stats.setdefault(c, {}), v)
            elif len(first) < 40:
                first.append((r, vals))
        for c, (n, total, lo, hi) in fast.items():      # fold the fast number counts into the column stats
            st = stats.setdefault(c, {})
            st["filled_rows"] = st.get("filled_rows", 0) + n
            st["numbers"] = st.get("numbers", 0) + n
            st["_sum"] = st.get("_sum", 0) + total
            st["min"] = lo if "min" not in st else min(st["min"], lo)
            st["max"] = hi if "max" not in st else max(st["max"], hi)
        headers = {c: str(v).strip() for c, v in enumerate(head[hdr - 1] if len(head) >= hdr else [], 1) if v not in (None, "")}
        for r, vals in first:                      # the first 30 rows were read before the header was known
            if r > hdr:
                for c, v in enumerate(vals, 1):
                    _stat_col(stats.setdefault(c, {}), v)
        totals = [r for r, vals in tail if any(isinstance(v, str) and TOTAL_RE.match(v.strip()) for v in list(vals)[:6])]
        for r, vals in tail:                       # a total row is not an entry: take it out of the column sums
            if r in totals:
                for c, v in enumerate(vals, 1):
                    if is_num(v) and "_sum" in stats.get(c, {}):
                        stats[c]["_sum"] -= v
                        stats[c]["numbers"] -= 1

        def row_dict(r, vals):
            return {"row": r, **{f"{col_letter(c)} {headers.get(c, '')}".strip(): short(v) if not hasattr(v, "isoformat") else str(v)
                                 for c, v in enumerate(vals, 1) if v not in (None, "")}}
        cols = []
        for c in sorted(set(headers) | set(stats)):
            st = dict(stats.get(c, {}))
            s = st.pop("_sum", None)
            item = {"col": col_letter(c), "header": headers.get(c, ""), **st}
            if s is not None and not id_like(headers.get(c, "")):
                item["sum_of_data_rows"] = round(s, 2)
            cols.append(item)
        s = {"sheet": ws.title, "header_row": hdr, "columns": cols[:40],
             "data_rows": f"{hdr + 1}-{(totals[0] - 1) if totals else n_rows}", "data_row_count": max(0, (totals[0] - 1 if totals else n_rows) - hdr)}
        if totals:
            s["total_rows"] = [row_dict(r, v) for r, v in tail if r in totals]
        if key:
            s["found"] = [row_dict(r, v) for r, v in hits]
        elif win:
            s["rows"] = [row_dict(r, v) for r, v in picked]
        else:
            s["first_rows"] = [row_dict(r, v) for r, v in first if r > hdr][:preview_rows]
        out["sheets"].append(s)
    wb.close()
    return out


# ---------------------------------------------------------------- what the agent sees before it changes anything
def layout(path: str, sheet: str | None = None, rows: str | None = None, find: str | None = None,
           preview_rows: int = 6) -> dict:
    low = path.lower()
    if low.endswith((".xlsx", ".xlsm")):
        from openpyxl import load_workbook
        probe = load_workbook(path, read_only=True)
        biggest = max(((ws.max_row or 0) for ws in probe.worksheets), default=0)
        probe.close()
        if biggest > BIG_ROWS:
            return _layout_big(path, sheet, rows, find, preview_rows)
    book = load(path)
    out = {"file": Path(path).name, "sheets": []}
    if book["notes"]:
        out["notes"] = book["notes"]
    if lost := lossy_parts(path):
        out["has"] = lost
        out["warning"] = (f"this file has {', '.join(lost)}. Editing it with the edit tool keeps the cells, formulas and formats "
                          "but drops those; tell the owner before you change it")
    for sh in book["sheets"]:
        if sheet and norm(sh.title) != norm(sheet):
            continue
        dr = sh.data_rows()
        s = {"sheet": sh.title, "header_row": sh.header_row,
             "columns": [sh.describe_column(c) for c in range(1, sh.max_col + 1)
                         if sh.headers.get(c) or any((r, c) in sh.cells for r in dr)],
             "data_rows": f"{sh.data_start}-{sh.data_end}" if dr else "none",
             "data_row_count": len(dr),
             "entries": sum(1 for r in dr if sh.has_content(r))}
        if sh.total_rows:
            s["total_rows"] = [sh.describe_row(r) for r in sh.total_rows]
        if sh.below:
            s["rows_below_totals"] = {"rows": f"{sh.below[0]}-{sh.below[-1]}", "count": len(sh.below),
                                      "first": [sh.describe_row(r) for r in sh.below[:3]],
                                      "note": "a separate block under the totals: not part of the data rows. Edits leave it "
                                              "alone unless include_rows_below_totals is true; ask the owner if it matters"}
        if find:
            key = norm(find)
            hits = [r for r in range(1, sh.last_row + 1)
                    if any(isinstance(v, str) and key and key in norm(v) for v in sh.row_vals(r))]
            s["found"] = [sh.describe_row(r) for r in hits[:25]]
            s["found_count"] = len(hits)
        elif rows:
            a, _, b = rows.partition("-")
            a, b = int(a), int(b or a)
            s["rows"] = [sh.describe_row(r) for r in range(max(1, a), min(b, sh.last_row) + 1)][:60]
        else:
            s["first_rows"] = [sh.describe_row(r) for r in dr[:preview_rows]]
        out["sheets"].append(s)
    if sheet and not out["sheets"]:
        out["error"] = f"no sheet '{sheet}'. Sheets: {', '.join(s.title for s in book['sheets'])}"
    return out


# ---------------------------------------------------------------- the edit
def _pick_sheet(book, name, column):
    sheets = book["sheets"]
    if name:
        for sh in sheets:
            if norm(sh.title) == norm(name):
                return sh, ""
        return None, f"no sheet '{name}'. Sheets: {', '.join(s.title for s in sheets)}"
    if len(sheets) == 1:
        return sheets[0], ""
    having = [sh for sh in sheets if column and sh.find_column(column)[0] and not sh.find_column(column)[1]]
    if len(having) == 1:
        return having[0], ""
    return None, (f"the file has {len(sheets)} sheets ({', '.join(s.title for s in sheets)}); say which sheet"
                  + (f" ('{column}' is in {', '.join(s.title for s in having)})" if having else ""))


def _cond(sh, cond: dict):
    c, err = sh.find_column(cond.get("column"))
    if err:
        raise ValueError(err)
    op, want = str(cond.get("op", "=")).strip(), cond.get("value")

    def ok(r):
        v = sh.ev.value(r, c)
        if op in ("empty", "is_empty"):
            return v in (None, "")
        if op in ("not_empty", "filled"):
            return v not in (None, "")
        if op == "contains":
            return norm(want) in norm(v)
        if op in ("=", "==", "!="):
            same = (as_number(v) == as_number(want)) if is_num(want) else norm(v) == norm(want)
            return same if op != "!=" else not same
        if v is UNK:
            raise ValueError(f"cannot work out the values of '{cond.get('column')}' (its formula is too complex); "
                             "pick the rows another way (names or row numbers)")
        x, y = as_number(v), as_number(want)
        if ERR in (x, y):
            return False
        return {"<": x < y, "<=": x <= y, ">": x > y, ">=": x >= y}.get(op, False)
    return ok


def _target_rows(sh, ch: dict, col: int, report: dict) -> list[int]:
    rows = ch.get("rows", "data")
    base = [r for r in sh.data_rows() if sh.has_content(r, skip_cols=(col,))]
    template = [r for r in sh.data_rows() if r not in base]
    if template:
        report["skipped_empty_rows"] = len(template)
    if ch.get("include_rows_below_totals"):
        base += [r for r in sh.below if sh.has_content(r)]
    elif sh.below:
        hit = [r for r in sh.below if sh.cells.get((r, col)) not in (None, "")]
        report["rows_below_totals_left_alone"] = {"rows": f"{sh.below[0]}-{sh.below[-1]}", "with_a_value_in_this_column": len(hit)}
    if ch.get("include_total_rows"):
        base += sh.total_rows
    elif sh.total_rows:
        report["total_rows_left_alone"] = sh.total_rows
    if isinstance(rows, list):
        nums = [int(r) for r in rows]
        bad = [r for r in nums if r <= sh.header_row]
        if bad:
            raise ValueError(f"rows {bad} are the header or above it")
        picked = sorted(set(nums))
    elif isinstance(rows, dict):
        picked = base
        if names := rows.get("names"):
            ncol, err = sh.find_column(rows.get("name_column") or _name_col(sh))
            if err:
                raise ValueError(err)
            want = {norm(n): n for n in names}
            picked = [r for r in base if norm(sh.cells.get((r, ncol))) in want]
            found = {norm(sh.cells.get((r, ncol))) for r in picked}
            if missing := [n for k, n in want.items() if k not in found]:
                report["names_not_found"] = missing
            twice = {n: [r for r in picked if norm(sh.cells.get((r, ncol))) == k] for k, n in want.items()}
            if twice := {n: rs for n, rs in twice.items() if len(rs) > 1}:
                if not rows.get("all_matches"):
                    raise ValueError("these names are on more than one row: "
                                     + "; ".join(f"{n} on rows {', '.join(map(str, rs))}" for n, rs in twice.items())
                                     + ". Ask the owner which one (e.g. by room), then pass row numbers; or all_matches: true")
        conds = rows.get("where") or []
        if isinstance(conds, dict):
            conds = [conds]
        for cond in conds:
            ok = _cond(sh, cond)
            picked = [r for r in picked if ok(r)]
    elif rows in ("data", "all", None):
        picked = base
    else:
        m = re.fullmatch(r"\s*(\d+)\s*-\s*(\d+)\s*", str(rows))
        if not m:
            raise ValueError("rows must be 'data', a list of row numbers, 'A-B', or {'where': [...]} / {'names': [...]}")
        picked = [r for r in base if int(m.group(1)) <= r <= int(m.group(2))]
    if ch.get("only_empty"):
        picked = [r for r in picked if sh.cells.get((r, col)) in (None, "")]
    if ch.get("only_filled"):
        picked = [r for r in picked if sh.cells.get((r, col)) not in (None, "")]
    return picked


def _name_col(sh) -> str:
    for c, h in sh.headers.items():
        if re.search(r"name|party|customer|student|naam", h, re.I):
            return h
    for c in range(1, sh.max_col + 1):
        if sum(1 for r in sh.data_rows() if isinstance(sh.cells.get((r, c)), str)) > len(sh.data_rows()) / 2:
            return col_letter(c)
    return "B"


def _new_value(sh, ch: dict, r: int):
    if "copy_from" in ch:
        c, err = sh.find_column(ch["copy_from"])
        if err:
            raise ValueError(err)
        v = sh.ev.value(r, c)
        if v is UNK:
            raise ValueError(f"cannot work out what '{ch['copy_from']}' shows in row {r} (its formula is too complex); "
                             f"use formula '={{{ch['copy_from']}}}' to copy it as a live formula instead")
        return v
    if "compute" in ch:          # "{Aug-26 Bill Amount} - {Aug-26 Paid Arpita}" -> a number per row
        expr = _substitute(sh, ch["compute"], r)
        ev = _Eval(sh.cells, sh.cached)
        ev.memo = sh.ev.memo
        v = ev._formula(expr.lstrip("="))
        if v is UNK or v == ERR:
            raise ValueError(f"could not compute '{ch['compute']}' (only + - * / and SUM/MIN/MAX/ROUND/ABS are supported)")
        return v
    if "formula" in ch:
        f = "=" + str(ch["formula"]).lstrip("=")
        if "{" in f:
            return "=" + _substitute(sh, f, r).lstrip("=")
        anchor = ch.get("_anchor")                 # a plain formula written for the first row is filled down, as Excel does
        if anchor and anchor[0] != r:
            from openpyxl.formula.translate import Translator
            return Translator(f, origin=f"{col_letter(anchor[1])}{anchor[0]}").translate_formula(f"{col_letter(anchor[1])}{r}")
        return f
    if "value" in ch:
        return ch["value"]
    raise ValueError(f"no new value for '{ch.get('column') or ch.get('header')}': each change carries its own value, formula, "
                     "copy_from or compute, e.g. {\"action\": \"add_column\", \"header\": \"Status\", \"formula\": \"=IF(G2>=0,\\\"Clear\\\",\\\"Pending\\\")\"}")


def _substitute(sh, template: str, r: int) -> str:
    def rep(m):
        key = m.group(1)
        if key == "r":
            return str(r)
        c, err = sh.find_column(key)
        if err:
            raise ValueError(err)
        return f"{col_letter(c)}{r}"
    return re.sub(r"\{([^{}]+)\}", rep, template)


MONTHS = {"jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec", "january", "february",
          "march", "april", "june", "july", "august", "september", "october", "november", "december"}


CLEAR_WORDS = re.compile(r"(clear|empty|blank|remove|delete|erase|hata|mita|khali|khaali|saaf|nikal|हटा|मिटा|खाली)", re.I)


def _words(text) -> list[str]:
    return re.findall(r"[a-z]+", str(text or "").lower())


def named_by_owner(header: str, letter: str, owner_words: str) -> bool:
    """Did the owner name this column? Its distinctive words ('paid arpita' of 'Aug-26 Paid Arpita') must all be in what
    they wrote (a word may be shortened: 'collect'), and if they named months, its month must be one of them. A column
    letter ('F column', 'column F') counts too."""
    said = set(_words(owner_words))
    low = str(owner_words or "").lower()
    if re.search(rf"\b(col(umn)?\s*{letter.lower()}|{letter.lower()}\s*col(umn)?)\b", low):
        return True
    hw = _words(header)
    key = [w for w in hw if w not in MONTHS and len(w) > 2]
    months = {w[:3] for w in hw if w in MONTHS}
    said_months = {w[:3] for w in said if w in MONTHS}

    def has(w):
        return any(s == w or (len(s) >= 4 and (w.startswith(s) or s.startswith(w))) for s in said)
    if not key:
        return bool(months & said_months)
    return all(has(w) for w in key) and (not months or not said_months or bool(months & said_months))


def added_here(writes, sh, col) -> bool:
    """A column an earlier step of this same call just added (its header was written by us)."""
    return any(w[0] is sh and w[1] == sh.header_row and w[2] == col and w[3] is None for w in writes)


def plan(book, changes: list[dict], owner_words: str = "") -> tuple[list, list]:
    """Work out every cell each change writes. Returns (writes, reports); writes are (sheet, row, col, old, new)."""
    writes, reports, claimed = [], [], {}
    for i, ch in enumerate(changes):
        action = ch.get("action", "set")
        target = ch.get("column") if action != "add_column" else (ch.get("after") or ch.get("header"))
        sh, err = _pick_sheet(book, ch.get("sheet"), target)
        if err:
            raise ValueError(f"change {i + 1}: {err}")
        rep = {"change": i + 1, "sheet": sh.title, "action": action}
        if action == "add_column":
            header = str(ch.get("header") or "").strip()
            if not header:
                raise ValueError(f"change {i + 1}: add_column needs header (the new column's title)")
            if sh.find_column(header)[0] and not sh.find_column(header)[1] and norm(sh.headers.get(sh.find_column(header)[0])) == norm(header):
                raise ValueError(f"change {i + 1}: a column '{header}' already exists; use action set on it")
            col = sh.max_col + 1
            if ch.get("after"):
                a, err = sh.find_column(ch["after"])
                if err:
                    raise ValueError(f"change {i + 1}: {err}")
                if a != sh.max_col:
                    rep["note"] = (f"the new column goes at the end ({col_letter(col)}), not right after "
                                   f"{col_letter(a)}: moving columns would break the file's formulas")
            sh.headers[col] = header
            sh.max_col = col
            writes.append((sh, sh.header_row, col, None, header))
            rep["column"] = f"{col_letter(col)} '{header}' (new)"
        else:
            col, err = sh.find_column(ch.get("column"))
            if err:
                raise ValueError(f"change {i + 1}: {err}")
            header = sh.headers.get(col, "")
            if owner_words and not added_here(writes, sh, col) and not named_by_owner(header, col_letter(col), owner_words):
                raise ValueError(f"change {i + 1}: the owner did not name the column {col_letter(col)} '{header}'. Change only "
                                 "the columns they named; if you think this one must change too, ask_owner first")
            if action == "clear" and owner_words and not CLEAR_WORDS.search(owner_words):
                raise ValueError(f"change {i + 1}: the owner did not ask to empty or remove anything; do not clear "
                                 f"'{header}'. Ask them if you think it should be")
            rep["column"] = f"{col_letter(col)} '{header}'"
        rows = _target_rows(sh, ch, col, rep)
        if not rows:
            rep["rows_changed"] = 0
            rep["warning"] = "no row matched; nothing changed for this step"
            reports.append(rep)
            continue
        n, sample, formulas_over, values_over = 0, [], 0, 0
        for r in rows:
            old = sh.cells.get((r, col))
            ch.setdefault("_anchor", (rows[0], col))
            new = None if action == "clear" else _new_value(sh, ch, r)
            if isinstance(new, float) and new.is_integer():
                new = int(new)
            if new == old:
                continue
            if (sh.title, r, col) in claimed:          # a later step refines an earlier one (add a column, then fill some rows)
                writes[:] = [w for w in writes if not (w[0] is sh and w[1] == r and w[2] == col)]
            claimed[(sh.title, r, col)] = i + 1
            if is_formula(old):
                formulas_over += 1
            elif old not in (None, ""):
                values_over += 1
            writes.append((sh, r, col, old, new))
            n += 1
            if len(sample) < 6:
                who = sh.cells.get((r, col_index(_name_col(sh)))) if _name_col(sh) else None
                sample.append(f"{col_letter(col)}{r}{f' ({short(who, 18)})' if isinstance(who, str) else ''}: "
                              f"{short(old) if old not in (None, '') else 'blank'} -> {short(new) if new not in (None, '') else 'blank'}")
        rep["rows_changed"] = n
        rep["examples"] = sample
        if formulas_over:
            rep["formulas_replaced"] = formulas_over
        if values_over:
            rep["values_replaced"] = values_over
        reports.append(rep)
    return writes, reports


def effects(book, writes) -> list[str]:
    """What the change does to the formulas that read the changed cells: totals that move, cells that will show #VALUE!."""
    by_sheet = {}
    for sh, r, c, old, new in writes:
        by_sheet.setdefault(sh.title, (sh, []))[1].append((r, c, new))
    out = []
    for title, (sh, ws) in by_sheet.items():
        changed = {(r, c) for r, c, _ in ws}
        before = _Eval(dict(sh.cells), sh.cached)
        after_cells = dict(sh.cells)
        for r, c, new in ws:
            if new is None:
                after_cells.pop((r, c), None)
            else:
                after_cells[(r, c)] = new
        after = _Eval(after_cells, {})
        errors, moved = {}, {}
        for (r, c), f in sh.cells.items():            # every formula, so a Total that sums a changed column is seen too
            if not is_formula(f) or (r, c) in changed:
                continue
            b, a = before.value(r, c), after.value(r, c)
            if a == ERR and b != ERR:
                errors.setdefault(c, []).append(r)
            elif b != a and is_num(a) and (is_num(b) or b is None) and UNK not in (a, b):
                moved.setdefault(c, []).append((r, b, a))
        for c, rs in sorted(errors.items()):
            out.append(f"{len(rs)} cells in {col_letter(c)} '{sh.headers.get(c, '')}' will show #VALUE! (they do arithmetic on "
                       f"the changed cells, which now hold text), e.g. {col_letter(c)}{rs[0]}")
        for c in sorted(set(moved) | set(errors)):          # where each affected column ends up, so a goal ("Total 0") is checkable
            rows = [r for r in sh.data_rows() if is_formula(sh.cells.get((r, c)))]

            def spread(ev):
                vals = [ev.value(r, c) for r in rows]
                neg = [r for r, v in zip(rows, vals) if is_num(v) and v < 0]
                return (len(neg), sum(1 for v in vals if is_num(v) and v == 0), sum(1 for v in vals if is_num(v) and v > 0), neg)
            b, a = spread(before), spread(after)
            if b[:3] != a[:3]:
                out.append(f"{col_letter(c)} '{sh.headers.get(c, '')}' rows below 0 / at 0 / above 0: before {b[0]}/{b[1]}/{b[2]}, "
                           f"after {a[0]}/{a[1]}/{a[2]}" + (f" (still below 0: rows {', '.join(map(str, a[3][:8]))})" if a[0] else ""))
        for c, ms in sorted(moved.items()):
            tot = [m for m in ms if m[0] in sh.total_rows]
            if tot:
                r, b, a = tot[0]
                out.append(f"total {col_letter(c)}{r} '{sh.headers.get(c, '')}' goes from {short(b)} to {short(round(a, 2))}")
            rows = [m for m in ms if m[0] not in sh.total_rows]
            if rows:
                out.append(f"{len(rows)} rows of {col_letter(c)} '{sh.headers.get(c, '')}' change too (their formula reads the "
                           f"changed column), e.g. row {rows[0][0]}: {short(rows[0][1])} -> {short(round(rows[0][2], 2))}")
    return out


def _cell_map(path: str) -> dict:
    book = load(path, big_ok=False)
    return {sh.title: sh.cells for sh in book["sheets"]}


def apply(src: str, dst: str, changes: list[dict], preview: bool = False, owner_words: str = "") -> dict:
    """Make the changes (or only say what they would do, preview=True). The result says exactly which cells changed and
    proves nothing else did: the saved file is read back and compared with the original, cell by cell."""
    if not isinstance(changes, list) or not changes:
        return {"error": "changes must be a list of steps, e.g. [{'column': 'Aug Collection', 'value': 'Paid'}]"}
    book = load(src, big_ok=False)
    try:
        writes, reports = plan(book, changes, owner_words)
    except ValueError as e:
        return {"error": str(e), "nothing_changed": True}
    result = {"changes": reports, "cells_to_change": len(writes)}
    if fx := effects(book, writes):
        result["effects"] = fx
    lost = lossy_parts(src)
    if lost:
        result["file_has"] = lost
    if preview:
        result["preview"] = True
        result["next"] = ("Nothing is saved yet. If this is what the owner's words say, save it (call again without preview) and "
                          "tell them the effects in one line; effects are to report, not a reason to ask. Ask only if their "
                          "words could mean a different change.")
        return result
    if not writes:
        return {**result, "saved": False, "note": "nothing to change: the cells already hold these values"}
    if lost and not any(ch.get("accept_loss") for ch in changes):
        return {**result, "saved": False, "error": f"saving this file would drop its {', '.join(lost)}. Ask the owner; if they "
                                                    "agree, repeat the call with accept_loss: true in the first change"}
    if book["kind"] == "csv":
        rows, delim = _read_csv(src)
        for sh, r, c, old, new in writes:
            while len(rows) < r:
                rows.append([])
            row = rows[r - 1]
            while len(row) < c:
                row.append("")
            row[c - 1] = "" if new is None else str(new)
        buf = io.StringIO()
        csv.writer(buf, delimiter=delim, lineterminator="\r\n").writerows(rows)
        with open(dst, "w", encoding="utf-8-sig", newline="") as f:
            f.write(buf.getvalue())
    else:
        from copy import copy
        from openpyxl import load_workbook
        wb = load_workbook(src, keep_vba=book["kind"] == "xlsm")
        for sh, r, c, old, new in writes:
            ws = wb[sh.title]
            cell = ws.cell(row=r, column=c)
            if new is None:
                cell.value = None
                continue
            if old is None and r != sh.header_row:        # a cell that was blank takes the look of the one above it
                above = ws.cell(row=max(r - 1, 1), column=c)
                if above.has_style and r - 1 > sh.header_row:
                    cell._style = copy(above._style)
            if old is None and r == sh.header_row:
                left = ws.cell(row=r, column=max(c - 1, 1))
                if left.has_style:
                    cell._style = copy(left._style)
            if isinstance(new, str) and not is_formula(new) and is_num(old):
                cell.number_format = "General"
            cell.value = new
        try:
            wb.calculation.fullCalcOnLoad = True           # Excel works the formulas out again when the file opens
        except Exception:
            pass
        wb.save(dst)
    # read back and prove that only the planned cells changed
    a, b = _cell_map(src), _cell_map(dst)
    planned = {(sh.title, r, c): new for sh, r, c, _, new in writes}
    outside, wrong = [], []
    for title in set(a) | set(b):
        ca, cb = a.get(title, {}), b.get(title, {})
        for key in set(ca) | set(cb):
            va, vb = ca.get(key), cb.get(key)
            p = (title, *key)
            if p in planned:
                exp = planned[p]
                if not (vb == exp or (exp is None and vb in (None, "")) or (book["kind"] == "csv" and str(vb) == str(exp))):
                    wrong.append(f"{title}!{col_letter(key[1])}{key[0]}")
            elif va != vb and not (book["kind"] == "csv" and str(va) == str(vb)):
                outside.append(f"{title}!{col_letter(key[1])}{key[0]}: {short(va)} -> {short(vb)}")
    result.update({"saved": True, "verified": not outside and not wrong,
                   "check": ("read the saved file back: only the cells listed above changed; every other cell, formula, row "
                             "and sheet is as in the original" if not outside and not wrong else
                             "MISMATCH after saving: do not send this file; tell the owner it could not be done safely"),
                   **({"changed_outside_plan": outside[:10]} if outside else {}),
                   **({"not_written": wrong[:10]} if wrong else {})})
    return result


def main(spec_path: str) -> dict:
    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    try:
        if spec["op"] == "layout":
            return layout(spec["src"], spec.get("sheet"), spec.get("rows"), spec.get("find"))
        return apply(spec["src"], spec["dst"], spec.get("changes") or [], bool(spec.get("preview")), spec.get("owner_words") or "")
    except ValueError as e:
        return {"error": str(e)}
