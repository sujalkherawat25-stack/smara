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
        self._groups = None
        self._shape()

    # -- a value written once for a whole group
    def group_columns(self) -> dict[int, dict]:
        """Columns among the first three that are written only on the FIRST row of each group (a room number, a party, a date) with the rows
        below left empty, as in a merged cell: {column: {(row, column): the value that belongs to that blank cell}}. Conservative on purpose: the
        first entry row has a value, at least 4 groups, a fifth to four fifths of the rows are empty, the values hardly repeat (a status or
        a remark column does), no formulas, short values. The block under the totals is carried on its own."""
        if self._groups is not None:
            return self._groups
        self._groups = {}
        rows = [r for r in self.data_rows() if self.has_content(r)]
        if len(rows) < 6:
            return self._groups
        for c in range(1, min(3, self.max_col) + 1):
            filled = [(r, self.cells.get((r, c))) for r in rows if self.cells.get((r, c)) not in (None, "")]
            if (len(filled) < 4 or filled[0][0] != rows[0] or not 0.2 * len(rows) <= len(rows) - len(filled) <= 0.8 * len(rows)
                    or any(is_formula(v) or (isinstance(v, str) and len(v) > 40) for _, v in filled)
                    or len({str(v).strip().lower() for _, v in filled}) < 0.9 * len(filled)):
                continue
            carry = {}
            for block in (rows, [r for r in self.below if self.has_content(r)]):
                last = None
                if block and self.cells.get((block[0], c)) in (None, ""):
                    continue
                for r in block:
                    v = self.cells.get((r, c))
                    if v not in (None, ""):
                        last = v
                    elif last is not None:
                        carry[(r, c)] = last
            self._groups[c] = carry
        return self._groups

    def carried(self, r: int, c: int):
        """What a blank cell in a group column stands for (None if the cell is not such a blank)."""
        g = self.group_columns().get(c)
        return g.get((r, c)) if g else None

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
            parsed = [num(t) for t in texts]
            if len(texts) >= 3 and sum(x is not None for x in parsed) >= 0.8 * len(texts) and not id_like(self.headers.get(c, "")):
                out["amounts_written_as_text"] = sum(x is not None for x in parsed)
                out["sum_of_those_amounts"] = round(sum(x for x in parsed if x is not None), 2)
                out["how_to_read_text_amounts"] = ("Dr is plus and Cr is minus; a bare amount is plus. Say that assumption in your answer. "
                                                   "query_spreadsheet adds such a column up exactly.")
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
        if c in self.group_columns():
            out["written_once_per_group"] = (f"only the first row of each group has a value ({out['filled_rows']} of the entry rows); the empty cells below "
                                             "belong to the same value, as in a merged cell. query_spreadsheet and edit_spreadsheet count them with it: "
                                             "a filter on this column covers the whole group, not just its first row")
        return out

    def describe_row(self, r: int) -> dict:
        row = {}
        for c in range(1, self.max_col + 1):
            v = self.cells.get((r, c))
            if v in (None, ""):
                if (g := self.carried(r, c)) is not None:
                    row[f"{col_letter(c)} {self.headers.get(c, '')}".strip() + " (group)"] = short(g)
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
        if len(rows) > BIG_ROWS and not big_ok:
            raise ValueError(f"the file has {len(rows):,} rows; open it with open_spreadsheet / query_spreadsheet")
        return {"kind": "csv", "delimiter": delim, "sheets": [Sheet("csv", cells, {}, len(rows), mc)], "notes": []}
    if low.endswith(".xls"):
        raise ValueError("this is an old .xls file. It cannot be changed in place; tell the owner, and offer to save the "
                         "change as a new .xlsx (formatting may differ) if they want")
    from openpyxl import load_workbook
    keep_vba = low.endswith(".xlsm")
    probe = load_workbook(path, read_only=True)
    sizes = {ws.title: (ws.max_row or 0) for ws in probe.worksheets}
    probe.close()
    big = max(sizes.values(), default=0) > BIG_ROWS or _sheet_xml_bytes(path) > BIG_SHEET_XML
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
    out = {"file": Path(path).name, "sheets": [], "big": True,
           "note": "a big file: summarised in one pass. Answer questions with query_spreadsheet (filters, groups, totals "
                   "over every row); edit_spreadsheet works row by row at this size (formatting is not kept)"}
    win = None
    if rows:
        a, _, b = str(rows).partition("-")
        win = (int(a), int(b or a))
    key = norm(find) if find else ""
    for title, stream in _streams(path, values=False):
        if sheet and norm(title) != norm(sheet):
            continue
        head, hdr, best = [], 1, 0
        stats, tail, picked, hits, n_rows, first, fast = {}, deque(maxlen=40), [], [], 0, [], {}
        for r, vals in enumerate(stream, 1):
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
        s = {"sheet": title, "header_row": hdr, "columns": cols[:40],
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
    return out


# ---------------------------------------------------------------- what the agent sees before it changes anything
def layout(path: str, sheet: str | None = None, rows: str | None = None, find: str | None = None,
           preview_rows: int = 6) -> dict:
    if is_big(path):
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
        if v in (None, "") and (g := sh.carried(r, c)) is not None:
            v = g                                       # a blank under a group's first row counts as that group
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


def _same_name_elsewhere(sh, rows: list[int], owner_words: str, col: int) -> str:
    """The owner named a person who is on more than one row of the sheet and nothing in their words says which row (a room or row number, or a
    figure that only one of the rows holds): the question to ask instead of guessing, '' when it is clear."""
    ncol, err = sh.find_column(_name_col(sh))
    if err or not ncol:
        return ""
    low = re.sub(r"file_ref=\S+", " ", str(owner_words)).lower()           # the attached file's own name is not the owner's words
    tokens = set(re.findall(r"\d{2,}", low)) | set(re.findall(r"(?:row|room|kamra|line)\s*(?:no\.?\s*)?(\d+)", low))     # a room, a row or a figure
    entries =[r for r in sh.data_rows() if sh.has_content(r)] + [r for r in sh.below if sh.has_content(r)]
    by_name: dict[str, list[int]] = {}
    for r in entries:
        v = sh.cells.get((r, ncol))
        if isinstance(v, str) and v.strip():
            by_name.setdefault(norm(v), []).append(r)

    def facts(r: int) -> set[str]:
        out = {str(v).strip().lower().removesuffix(".0") for v in sh.row_vals(r) if v not in (None, "") and not is_formula(v)}
        out |= {str(g).removesuffix(".0") for c in sh.group_columns() if (g := sh.carried(r, c)) is not None}
        return out | {str(r)}

    for r in rows:
        name = sh.cells.get((r, ncol))
        if not isinstance(name, str) or not name.strip():
            continue
        others = [o for o in by_name.get(norm(name), []) if o not in rows]
        if not others or not re.search(rf"(?<![a-z]){re.escape(name.strip().lower())}(?![a-z])", low):
            continue
        mine = facts(r)
        if any(t in mine and all(t not in facts(o) for o in others) for t in tokens):
            continue                                                         # a room, a row or a figure that only this row has
        def where(x: int) -> str:
            room = next((sh.carried(x, c) if sh.cells.get((x, c)) in (None, "") else sh.cells.get((x, c)) for c in sh.group_columns()), None)
            now = sh.cells.get((x, col))
            return f"row {x}" + (f", {sh.headers.get(next(iter(sh.group_columns())))} {room}" if room is not None else "") + (f", now {short(now)}" if now not in (None, "") else ", now empty")
        every = sorted({r, *others})
        return (f"'{name.strip()}' is on {len(every)} rows ({'; '.join(where(x) for x in every)}) and the owner did not say which one. Do not guess: "
                "ask_owner which one (one option per row, with its room or row and what it holds now). Once they have answered, repeat this change "
                "with \"owner_confirmed_row\": true.")
    return ""


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
        if isinstance(v, float):
            v = round(v, 10)          # 1404 * 0.9 is 1263.6000000000001 in binary: Excel stores 16 digits, so the read-back must see 1263.6
        return v
    if "formula" in ch or (isinstance(ch.get("value"), str) and (ch["value"].lstrip().startswith("=") or re.search(r"\{[^{}]+\}", ch["value"]))):
        # a formula the model put in `value` is still a formula: it was written into the cell with its {Header} placeholders
        # unresolved, the check passed, and the owner got a payroll file full of #NAME? errors
        f = "=" + str(ch["formula"] if "formula" in ch else ch["value"]).strip().lstrip("=")
        if "{" in f:
            f = "=" + _substitute(sh, f, r).lstrip("=")
        if "{" in f or "}" in f:
            raise ValueError(f"the formula {f[:80]!r} still has a {{ }} that is not a column header; write the column's exact title in the braces, e.g. {{Basic}}")
        if "formula" not in ch or "{" in str(ch["formula"]):
            return f
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


FILLER_WORDS = {"amount", "amt", "rupees", "rupee", "inr"}          # words in a header that only say 'this is money'


CLEAR_WORDS = re.compile(r"(clear|empty|blank|remove|delete|erase|hata|mita|khali|khaali|saaf|nikal|हटा|मिटा|खाली)", re.I)


def _words(text) -> list[str]:
    return re.findall(r"[a-z]+", str(text or "").lower())


def look_alike_columns(sh, col: int) -> list[int]:
    """Other NUMBER columns whose header starts with all the words of this column's header and has more ('Amount' -> 'Amount Received', 'Amount Pending'),
    so an owner who says only 'Amount' could mean any of them."""
    words = _words(sh.headers.get(col, ""))
    if not words:
        return []
    found = []
    for c, h in sh.headers.items():
        w = _words(h)
        if c != col and len(w) > len(words) and w[:len(words)] == words and any(
                cc == c and (isinstance(v, (int, float)) and not isinstance(v, bool) or is_formula(v)) for (r, cc), v in sh.cells.items()):
            found.append(c)
    return found


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
    if len(key) > 1:                       # 'Bill Amount': the owner who says 'bill' named it; 'amount' only says it is money
        key = [w for w in key if w not in FILLER_WORDS] or key
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
        target = ch.get("column") if action != "add_column" else (ch.get("after") or ch.get("header") or ch.get("name") or ch.get("column"))
        sh, err = _pick_sheet(book, ch.get("sheet"), target)
        if err:
            raise ValueError(f"change {i + 1}: {err}")
        rep = {"change": i + 1, "sheet": sh.title, "action": action}
        if action == "add_column":
            header = str(ch.get("header") or ch.get("name") or ch.get("column") or "").strip()
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
            if action == "set" and owner_words and not ch.get("owner_confirmed_column") and not added_here(writes, sh, col):
                alike = look_alike_columns(sh, col)
                if alike and not any(norm(sh.headers[c]) in norm(owner_words) for c in alike):       # the owner did not name any longer one
                    raise ValueError(f"change {i + 1}: {col_letter(col)} '{header}' is also how other number columns start: "
                                     + ", ".join(f"{col_letter(c)} '{sh.headers[c]}'" for c in alike)
                                     + ". The owner's words may mean any of them, so do not guess: ask_owner which column (option 1 = the column named exactly "
                                       f"'{header}', then the others; one option can be 'all of them'). Once they have answered, repeat this change with \"owner_confirmed_column\": true.")
            if action == "clear" and owner_words and not CLEAR_WORDS.search(owner_words):
                raise ValueError(f"change {i + 1}: the owner did not ask to empty or remove anything; do not clear "
                                 f"'{header}'. Ask them if you think it should be")
            rep["column"] = f"{col_letter(col)} '{header}'"
        if action == "fill_from":
            _plan_fill_from(book, sh, col, ch, rep, writes, claimed, i)
            reports.append(rep)
            continue
        rows = _target_rows(sh, ch, col, rep)
        if not rows:
            rep["rows_changed"] = 0
            rep["warning"] = "no row matched; nothing changed for this step"
            reports.append(rep)
            continue
        if action in ("set", "clear") and owner_words and not ch.get("owner_confirmed_row") and len(rows) <= 12:
            if why := _same_name_elsewhere(sh, rows, owner_words, col):
                raise ValueError(f"change {i + 1}: {why}")
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


def _plan_fill_from(book, sh, col, ch, rep, writes, claimed, i) -> None:
    """A lookup that never guesses: fill the EMPTY cells of `column` from another sheet by matching a key (a party name, an item code).
    Keys match ignoring case, spaces, punctuation, M/s and Pvt/Ltd. A key that gives two different values in the other sheet (the same
    party with two GSTINs) or no value at all is left empty and listed, never picked."""
    src = next((s for s in book["sheets"] if norm(s.title) == norm(ch.get("from_sheet"))), None)
    if src is None:
        raise ValueError(f"change {i + 1}: no sheet called {ch.get('from_sheet')!r} (sheets: {', '.join(s.title for s in book['sheets'])})")
    if not ch.get("match_on"):
        raise ValueError(f"change {i + 1}: fill_from needs match_on (the column in this sheet that identifies the row, e.g. 'Party Name')")
    key_t, err = sh.find_column(ch["match_on"])
    if err:
        raise ValueError(f"change {i + 1}: {err}")
    key_s, err = src.find_column(ch.get("from_key") or ch["match_on"])
    if err:
        raise ValueError(f"change {i + 1}: in sheet {src.title!r}: {err}")
    val_s, err = src.find_column(ch.get("from_value") or ch.get("column"))
    if err:
        raise ValueError(f"change {i + 1}: in sheet {src.title!r}: {err}")
    table: dict = {}
    for r in src.data_rows():
        k, v = _name_key(src.ev.value(r, key_s)), src.ev.value(r, val_s)
        if k and v not in (None, ""):
            table.setdefault(k, {}).setdefault(str(v).strip(), v)
    filled, ambiguous, missing = [], {}, {}
    for r in sh.data_rows():
        old = sh.cells.get((r, col))
        if old not in (None, ""):
            continue
        name = sh.ev.value(r, key_t)
        k = _name_key(name)
        if not k:
            continue
        vals = table.get(k)
        label = str(name).strip()
        if vals is None:
            missing.setdefault(label, []).append(r)
        elif len(vals) > 1:
            ambiguous.setdefault(label, {"rows": [], "values": sorted(vals)})["rows"].append(r)
        else:
            claimed[(sh.title, r, col)] = i + 1
            writes.append((sh, r, col, old, next(iter(vals.values()))))
            filled.append(r)
    rep["rows_changed"] = len(filled)
    rep["filled_from"] = f"{src.title}: {sh.headers.get(key_t, '')} -> {src.headers.get(val_s, '')}"
    if ambiguous:
        rep["left_empty_two_values_in_the_other_sheet"] = [{"name": n, "rows": v["rows"][:6], "values": v["values"]} for n, v in ambiguous.items()]
    if missing:
        rep["left_empty_not_found_in_the_other_sheet"] = [{"name": n, "rows": rs[:6]} for n, rs in missing.items()]
    rep["still_empty"] = sum(len(v["rows"]) for v in ambiguous.values()) + sum(len(v) for v in missing.values())
    rep["examples"] = [f"{col_letter(col)}{r}: blank -> {next(iter(table[_name_key(sh.ev.value(r, key_t))].values()))}" for r in filled[:4]]
    if rep["still_empty"]:
        rep["note"] = "rows left empty on purpose: tell the owner which names and why (two different values, or not in the other sheet)"


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


TOTAL_WORDS = re.compile(r"(total|kul|jod|sum)", re.I)


def outside_what_was_asked(original: str, edited: str, owner_words: str) -> list[str]:
    """Problems with an edited copy of the owner's sheet, however it was made: a column they did not name that changed, cells
    emptied when they did not ask to remove anything, a Total row or the block under it touched when they did not mention it.
    Empty list = fine. Added columns and new sheets are fine (they are additions)."""
    a, b = load(original, big_ok=False), load(edited, big_ok=False)
    by_title = {s.title: s for s in b["sheets"]}
    problems, clears = [], CLEAR_WORDS.search(owner_words or "")
    for sa in a["sheets"]:
        sb = by_title.get(sa.title)
        if sb is None:
            problems.append(f"sheet '{sa.title}' is missing")
            continue
        cols, emptied, protected = set(), 0, set()
        for key in set(sa.cells) | set(sb.cells):
            va, vb = sa.cells.get(key), sb.cells.get(key)
            if va == vb or key[1] > sa.max_col:
                continue
            cols.add(key[1])
            if vb in (None, "") and va not in (None, ""):
                emptied += 1
            if key[0] in sa.total_rows or key[0] in sa.below:
                protected.add(key[0])
        for c in sorted(cols):
            h = sa.headers.get(c, "")
            if not named_by_owner(h, col_letter(c), owner_words):
                problems.append(f"column {col_letter(c)} '{h}' changed, but the owner did not name it")
        if emptied and not clears:
            problems.append(f"{emptied} cells were emptied, but the owner did not ask to remove anything")
        if protected and not TOTAL_WORDS.search(owner_words or ""):
            problems.append(f"the total row / rows under the totals changed (rows {', '.join(map(str, sorted(protected)[:6]))})")
    return problems


# ---------------------------------------------------------------- questions answered from every row, not by eye
# "Who still owes", "revenue by country", "how many returns": a model reading a few preview rows guesses these. Here a
# small spec (filters, groups, measures) is run over every entry row, any size, and the result carries its own counts
# (rows matched, rows skipped because a cell was not a number) so the answer can be quoted, not computed by the model.
import ast
import datetime as _dt

_DATE_RE = re.compile(r"^\s*(\d{1,4})[/.-](\d{1,2})[/.-](\d{1,4})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?\s*$")
_MON = {m[:3]: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_DMY_RE = re.compile(r"^\s*(\d{1,2})[ -/]([A-Za-z]{3})[a-z]*[ -/,]*(\d{2,4})\s*$")


def as_date(v, dayfirst: bool = True):
    """A cell as a datetime: Excel dates, '2011-03-05', '05/03/2011' (day first unless the column shows month first),
    '5-Mar-2011'. None when it is not a date."""
    if isinstance(v, _dt.datetime):
        return v
    if isinstance(v, _dt.date):
        return _dt.datetime(v.year, v.month, v.day)
    if not isinstance(v, str):
        return None
    m = _DATE_RE.match(v)
    try:
        if m:
            a, b, c = int(m.group(1)), int(m.group(2)), int(m.group(3))
            hh, mm, ss = int(m.group(4) or 0), int(m.group(5) or 0), int(m.group(6) or 0)
            if len(m.group(1)) == 4:
                y, mo, d = a, b, c
            else:
                y = c + (2000 if c < 100 else 0)
                d, mo = (a, b) if dayfirst else (b, a)
            return _dt.datetime(y, mo, d, hh, mm, ss)
        m = _DMY_RE.match(v)
        if m and m.group(2).lower()[:3] in _MON:
            y = int(m.group(3))
            return _dt.datetime(y + (2000 if y < 100 else 0), _MON[m.group(2).lower()[:3]], int(m.group(1)))
    except ValueError:
        return None
    return None


def num(v):
    """A cell as a number for a total, or None: '1,250', '₹ 1,250.50', '(300)' = -300 count; other text does not."""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, str):
        t = v.strip()
        sign = 1
        m = re.search(r"\s*\b(dr|cr)\b\.?\s*$", t, re.I)             # "12,500.00 Dr" / "3,200 Cr": debit is plus, credit is minus
        if m:
            sign, t = (-1 if m.group(1).lower() == "cr" else 1), t[:m.start()]
        t = t.replace(",", "").replace("₹", "").replace("Rs.", "").replace("Rs", "").strip()
        neg = t.startswith("(") and t.endswith(")")
        t = t.strip("()").strip()
        try:
            x = float(t) if t and t not in (".", "-") else None
        except ValueError:
            return None
        if x is not None and x.is_integer() and "." not in t:
            x = int(x)
        if x is None:
            return None
        return sign * (-x if neg else x)
    return None


class _Head:
    """The header of a streamed sheet, enough to find columns by name the same way as a loaded one."""
    find_column = Sheet.find_column

    def __init__(self, title: str, headers: dict):
        self.title, self.headers = title, headers
        self.max_col = max(headers, default=0)


_EXPR_NODES = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, ast.Name, ast.Load, ast.Add, ast.Sub, ast.Mult,
               ast.Div, ast.Pow, ast.Mod, ast.USub, ast.UAdd)


def _value_fn(spec, head):
    """'Quantity' or '{Quantity} * {UnitPrice}' -> (fn(get) -> value, label, columns used). An expression gives a number
    or None (a cell that is not a number makes the row count as skipped, never as 0)."""
    text = str(spec or "").strip()
    if not text:
        raise ValueError("say which column (its header)")
    if "{" not in text:
        c, err = head.find_column(text)
        if err:
            raise ValueError(err)
        return (lambda get: get(c)), head.headers.get(c) or col_letter(c), [c]
    cols = []

    def rep(m):
        c, err = head.find_column(m.group(1))
        if err:
            raise ValueError(err)
        if c not in cols:
            cols.append(c)
        return f"_c{c}"
    src = re.sub(r"\{([^{}]+)\}", rep, text)
    try:
        tree = ast.parse(src, mode="eval")
    except SyntaxError:
        raise ValueError(f"'{text}' is not a sum I can work out: use {{Column}} names with + - * / and numbers")
    for node in ast.walk(tree):
        if not isinstance(node, _EXPR_NODES) or (isinstance(node, ast.Name) and not node.id.startswith("_c")) or \
                (isinstance(node, ast.Constant) and not is_num(node.value)):
            raise ValueError(f"'{text}': only {{Column}} names, numbers and + - * / are allowed")
    code = compile(tree, "<expr>", "eval")

    def fn(get):
        env = {}
        for c in cols:
            x = num(get(c))
            if x is None:
                return None
            env[f"_c{c}"] = x
        try:
            return eval(code, {"__builtins__": {}}, env)
        except (ZeroDivisionError, OverflowError):
            return None
    fn.is_expr = True                      # a row it cannot work out is reported as skipped, never counted as 0
    return fn, text, cols


def _same(v, want, dayfirst=True) -> bool:
    if isinstance(want, bool):
        return v is want
    w = num(want) if not isinstance(want, (_dt.date, _dt.datetime)) else None
    if w is not None:
        x = num(v)
        if x is not None:
            return abs(x - w) < 1e-9
    wd = as_date(want) if isinstance(want, str) else None
    if wd is not None and (vd := as_date(v, dayfirst)) is not None:
        return vd.date() == wd.date() if len(str(want).strip()) <= 10 else vd == wd
    return str(v if v is not None else "").strip().casefold() == str(want if want is not None else "").strip().casefold()


def _equals(want, dayfirst=True):
    """_same with the wanted value worked out once (a filter runs it on every row)."""
    if isinstance(want, bool) or isinstance(want, (_dt.date, _dt.datetime)):
        return lambda v: _same(v, want, dayfirst)
    w = num(want)
    wd = as_date(want) if isinstance(want, str) and w is None else None
    wt = str(want if want is not None else "").strip().casefold()
    day_only = wd is not None and len(str(want).strip()) <= 10

    def eq(v):
        if w is not None:
            x = num(v)
            if x is not None:
                return abs(x - w) < 1e-9
        if wd is not None and (vd := as_date(v, dayfirst)) is not None:
            return vd.date() == wd.date() if day_only else vd == wd
        if isinstance(v, str):
            return v.strip().casefold() == wt
        return str(v if v is not None else "").strip().casefold() == wt
    return eq


QUERY_OPS = ("=", "!=", "<", "<=", ">", ">=", "between", "in", "not_in", "contains", "not_contains", "starts_with",
             "not_starts_with", "ends_with", "empty", "not_empty")


_OP_ALIAS = {"==": "=", "eq": "=", "equals": "=", "is": "=", "<>": "!=", "ne": "!=", "neq": "!=", "not": "!=", "gt": ">",
             "gte": ">=", "ge": ">=", "lt": "<", "lte": "<=", "le": "<=", "is_empty": "empty", "isnull": "empty", "null": "empty",
             "blank": "empty", "filled": "not_empty", "not_blank": "not_empty", "notnull": "not_empty", "startswith": "starts_with",
             "begins_with": "starts_with", "not_startswith": "not_starts_with", "endswith": "ends_with", "has": "contains",
             "includes": "contains", "like": "contains", "nin": "not_in", "range": "between"}
_COND_KEYS = {"column", "col", "field", "name", "expr", "op", "operator", "value", "values", "val", "is", "equals"}


def _plain_cond(cond) -> dict:
    """The condition as {'column', 'op', 'value'} whichever way it was written: 'operator' for 'op', 'field' for 'column',
    {'Country': 'Germany'} or {'InvoiceNo': {'startsWith': 'C'}} (the shapes models reach for first)."""
    if not isinstance(cond, dict):
        raise ValueError("each where condition is {'column': 'Country', 'op': '=', 'value': 'Germany'}")
    col = cond.get("column") or cond.get("col") or cond.get("field") or cond.get("name") or cond.get("expr")
    op = cond.get("op") or cond.get("operator")
    if "value" in cond or "values" in cond or "val" in cond:
        val = cond.get("value", cond.get("values", cond.get("val")))
    else:
        val = cond.get("equals", cond.get("is"))
    if col:
        return {"column": col, "op": op or ("in" if isinstance(val, list) else "="), "value": val}
    extra = [k for k in cond if k not in _COND_KEYS]
    if len(extra) == 1:
        k, v = extra[0], cond[extra[0]]
        if isinstance(v, dict) and len(v) == 1:
            o, w = next(iter(v.items()))
            o = str(o).lstrip("$")
            return {"column": k, "op": o, "value": w.strip("%") if isinstance(w, str) and o == "like" else w}
        return {"column": k, "op": "in" if isinstance(v, list) else "=", "value": v}
    raise ValueError("a where condition needs a column: {'column': 'Country', 'op': '=', 'value': 'Germany'}; ops: "
                     + ", ".join(QUERY_OPS))


def _cond_fn(cond: dict, head, dayfirst):
    cond = _plain_cond(cond)
    get_v, label, _ = _value_fn(cond.get("column"), head)
    op = str(cond.get("op") or "=").strip().lower().replace(" ", "_")
    op = _OP_ALIAS.get(op, op)
    if op not in QUERY_OPS:
        raise ValueError(f"op '{op}' is not one of: {', '.join(QUERY_OPS)}")
    want = cond.get("value")
    if op in ("empty", "not_empty"):
        return lambda get: (get_v(get) in (None, "")) == (op == "empty")
    if op in ("contains", "not_contains", "starts_with", "not_starts_with", "ends_with"):
        w = str(want if want is not None else "").strip().casefold()
        if op == "contains":
            return lambda get: w in str(get_v(get) if get_v(get) is not None else "").casefold()
        if op == "not_contains":
            return lambda get: w not in str(get_v(get) if get_v(get) is not None else "").casefold()

        def text(get):
            v = get_v(get)
            return str(int(v) if isinstance(v, float) and v.is_integer() else (v if v is not None else "")).strip().casefold()
        if op == "starts_with":
            return lambda get: text(get).startswith(w)
        if op == "not_starts_with":
            return lambda get: not text(get).startswith(w)
        return lambda get: text(get).endswith(w)
    if op in ("in", "not_in"):
        opts = want if isinstance(want, list) else [want]
        texts = {str(o).strip().casefold() for o in opts if num(o) is None and as_date(o) is None}
        nums = {num(o) for o in opts if num(o) is not None}
        others = [_equals(o, dayfirst) for o in opts if num(o) is None and as_date(o) is not None]

        def hit(v):
            if isinstance(v, str) and v.strip().casefold() in texts:
                return True
            x = num(v)
            if x is not None and x in nums:
                return True
            if isinstance(v, (int, float)) and str(v) in texts:
                return True
            return any(e(v) for e in others)
        return (lambda get: hit(get_v(get))) if op == "in" else (lambda get: not hit(get_v(get)))
    if op in ("=", "!="):
        eq = _equals(want, dayfirst)
        return (lambda get: eq(get_v(get))) if op == "=" else (lambda get: not eq(get_v(get)))
    lo_hi = want if op == "between" else [want]
    if op == "between" and not (isinstance(want, list) and len(want) == 2):
        raise ValueError("between needs value: [low, high] (both included)")
    as_nums = [num(x) for x in lo_hi]
    if all(x is not None for x in as_nums):
        def key(get):
            return num(get_v(get))
        bounds = as_nums
    else:
        dates = [as_date(x) if isinstance(x, str) else as_date(x) for x in lo_hi]
        if any(d is None for d in dates):
            raise ValueError(f"'{label}' {op} {want!r}: give a number or a date like 2011-03-31")
        if op in ("<=", "between") and isinstance(lo_hi[-1], str) and len(lo_hi[-1].strip()) <= 10:
            dates[-1] = dates[-1].replace(hour=23, minute=59, second=59)       # up to a date = the whole of that day

        def key(get):
            return as_date(get_v(get), dayfirst)
        bounds = dates
    b = bounds[0]
    cmp = {"<": lambda x: x < b, "<=": lambda x: x <= b, ">": lambda x: x > b, ">=": lambda x: x >= b,
           "between": lambda x: bounds[0] <= x <= bounds[1]}[op]

    def ok(get):
        x = key(get)
        return x is not None and cmp(x)
    return ok


_BY = {"year": lambda d: f"{d.year}", "quarter": lambda d: f"{d.year}-Q{(d.month - 1) // 3 + 1}",
       "month": lambda d: f"{d.year}-{d.month:02d}", "week": lambda d: f"{d.isocalendar()[0]}-W{d.isocalendar()[1]:02d}",
       "day": lambda d: d.strftime("%Y-%m-%d"), "weekday": lambda d: d.strftime("%a"), "hour": lambda d: f"{d.hour:02d}:00",
       "fy": lambda d: f"FY{d.year if d.month >= 4 else d.year - 1}-{(d.year + 1 if d.month >= 4 else d.year) % 100:02d}"}


_NAME_NOISE = {"m", "s", "ms", "mrs", "mr", "messrs", "shri", "shree", "the", "pvt", "private", "ltd", "limited", "llp", "co", "company"}


def _name_key(s) -> str:
    """Spelling-proof key for a party name: case, spaces, punctuation, M/s, Messrs, Pvt/Ltd, '&' and plurals do not count."""
    t = re.sub(r"[^a-z0-9 ]+", " ", str(s or "").lower().replace("&", " and "))
    out = []
    for w in t.split():
        if w in _NAME_NOISE:
            continue
        out.append(w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w)
    return " ".join(out)


class _NameKey:
    """group_by {column, by: 'name'}: gathers the spellings of one party under one key and remembers what it gathered."""

    def __init__(self, get_v):
        self.get_v, self.seen = get_v, {}

    def __call__(self, get):
        v = self.get_v(get)
        if not isinstance(v, str) or not v.strip():
            return "(blank)"
        k = _name_key(v) or v.strip().lower()
        c = self.seen.setdefault(k, {})
        c[v.strip()] = c.get(v.strip(), 0) + 1
        return k

    def pretty(self, k):
        c = self.seen.get(k)
        if not c:
            return k
        best = max(c, key=lambda s: (c[s], -len(s)))
        best = re.sub(r"^(m/s|messrs)\s+", "", best, flags=re.I).strip(" .,")
        return best.title() if best.isupper() or best.islower() else best

    def spellings(self, k):
        return sorted(self.seen.get(k, {}), key=lambda s: -self.seen[k][s])

    def look_alikes(self):
        import difflib
        keys = [k for k in self.seen if k != "(blank)"][:300]
        out = []
        for i, a in enumerate(keys):
            for b in keys[i + 1:]:
                sc = difflib.SequenceMatcher(None, a, b).ratio()
                if sc >= 0.8:
                    out.append({"a": self.pretty(a), "b": self.pretty(b), "score": round(sc, 2)})
        return out[:10]


def _group_fn(g, head, dayfirst):
    if isinstance(g, str):
        g = {"column": g}
    get_v, label, _ = _value_fn(g.get("column") or g.get("field") or g.get("of"), head)
    by = str(g.get("by") or "").lower()
    if by in ("name", "party", "party_name"):
        return _NameKey(get_v), f"{label}"
    if not by:
        def key(get):
            v = get_v(get)
            if isinstance(v, str):
                return v.strip()
            if isinstance(v, float) and v.is_integer():
                return int(v)
            return v if v is not None else "(blank)"
        return key, label
    if by not in _BY:
        raise ValueError(f"group by '{by}': one of {', '.join(_BY)}")
    f = _BY[by]

    def key(get):
        d = as_date(get_v(get), dayfirst)
        return f(d) if d else "(not a date)"
    return key, f"{label} ({by})"


MEASURE_OPS = ("count", "sum", "avg", "min", "max", "count_distinct", "median")


class _Query:
    def __init__(self, q: dict, head, dayfirst):
        if not isinstance(q, dict):
            raise ValueError("each query is an object with where / group_by / measures")
        self.q = q
        conds = q.get("where") or []
        self.where = [_cond_fn(c, head, dayfirst) for c in (conds if isinstance(conds, list) else [conds])]
        gb = q.get("group_by") or []
        self.groups = [_group_fn(g, head, dayfirst) for g in (gb if isinstance(gb, list) else [gb])]
        ms = q.get("measures") or [{"op": "count"}]
        ms = ms if isinstance(ms, list) else [ms]
        self.measures = []
        for m in ms:
            if isinstance(m, str):
                m = {"op": "sum", "of": m}
            if not isinstance(m, dict):
                raise ValueError("each measure is {'op': 'sum', 'of': 'Amount'} (op: " + ", ".join(MEASURE_OPS) + ")")
            op = str(m.get("op") or m.get("agg") or m.get("function") or "sum").lower()
            op = {"average": "avg", "mean": "avg", "total": "sum", "distinct": "count_distinct", "unique": "count_distinct",
                  "nunique": "count_distinct", "len": "count"}.get(op, op)
            if op not in MEASURE_OPS:
                raise ValueError(f"measure op '{op}': one of {', '.join(MEASURE_OPS)}")
            of = m.get("of") or m.get("column") or m.get("field") or m.get("expr")
            if op == "count" and not of:
                self.measures.append((op, None, m.get("as") or "rows"))
                continue
            fn, label, _ = _value_fn(of, head)
            self.measures.append((op, fn, m.get("as") or f"{op} of {label}"))
        self.list_rows = bool(q.get("list"))
        self.columns = [head.find_column(c) for c in (q.get("columns") or [])]
        if bad := [e for _, e in self.columns if e]:
            raise ValueError(bad[0])
        self.head = head
        self.limit = max(1, min(int(q.get("limit") or (30 if self.list_rows else 20)), 200))
        self.acc, self.matched, self.skipped = {}, 0, {}
        self.sample = []
        sort = str(q.get("sort") or "").strip()
        self.sort_desc = not sort.lower().endswith(" asc")
        self.sort = re.sub(r"\s+(asc|desc)$", "", sort, flags=re.I)
        self.sort_fn = None
        if self.list_rows and self.sort:
            self.sort_fn = _value_fn(self.sort, head)[0]

    def add(self, r: int, get, row_fn):
        for ok in self.where:
            if not ok(get):
                return
        self.matched += 1
        key = tuple(k(get) for k, _ in self.groups)
        st = self.acc.get(key)
        if st is None:
            st = self.acc[key] = [[0, 0, None, None, set() if op == "count_distinct" else ([] if op == "median" else None)]
                                  for op, _, _ in self.measures]
        for (op, fn, name), s in zip(self.measures, st):
            if fn is None:
                s[1] += 1
                continue
            v = fn(get)
            if op == "count":
                if v not in (None, ""):
                    s[1] += 1
                continue
            if op == "count_distinct":
                if v not in (None, ""):
                    s[4].add(v.strip().casefold() if isinstance(v, str) else v)
                continue
            x = num(v)
            if x is None:
                if v not in (None, "") or getattr(fn, "is_expr", False):
                    self.skipped[name] = self.skipped.get(name, 0) + 1
                continue
            s[0] += x
            s[1] += 1
            s[2] = x if s[2] is None or x < s[2] else s[2]
            s[3] = x if s[3] is None or x > s[3] else s[3]
            if op == "median":
                s[4].append(x)
        if self.list_rows:
            if self.sort_fn is not None:
                import heapq
                x = num(self.sort_fn(get))
                if x is None:
                    return
                key = x if self.sort_desc else -x
                if len(self.sample) < self.limit:
                    heapq.heappush(self.sample, (key, r, row_fn()))
                elif key > self.sample[0][0]:
                    heapq.heapreplace(self.sample, (key, r, row_fn()))
            elif len(self.sample) < self.limit:
                self.sample.append((0, r, row_fn()))

    @staticmethod
    def _final(op, s):
        if op == "sum":
            return round(s[0], 2) if s[1] else 0
        if op == "count":
            return s[1]
        if op == "count_distinct":
            return len(s[4])
        if op == "avg":
            return round(s[0] / s[1], 2) if s[1] else None
        if op == "min":
            return s[2]
        if op == "max":
            return s[3]
        if op == "median":
            v = sorted(s[4])
            n = len(v)
            return None if not n else round(v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2, 2)

    def result(self) -> dict:
        out = {"rows_matched": self.matched}
        names = [n for _, _, n in self.measures]
        if self.skipped:
            out["not_numbers_skipped"] = self.skipped
        if not self.groups:
            st = self.acc.get((), [[0, 0, None, None, set() if op == "count_distinct" else []] for op, _, _ in self.measures])
            out["result"] = {n: self._final(op, s) for (op, _, n), s in zip(self.measures, st)}
        else:
            rows = []
            for key, st in self.acc.items():
                row = {label: (kf.pretty(k) if isinstance(kf, _NameKey) else k) for (kf, label), k in zip(self.groups, key)}
                row.update({n: self._final(op, s) for (op, _, n), s in zip(self.measures, st)})
                for (kf, label), k in zip(self.groups, key):
                    if isinstance(kf, _NameKey) and len(kf.seen.get(k, {})) > 1:
                        row["spellings_gathered"] = kf.spellings(k)
                rows.append(row)
            by_time = any(isinstance(g, dict) and g.get("by") for g in
                          (self.q.get("group_by") if isinstance(self.q.get("group_by"), list) else [self.q.get("group_by")]))
            sort = self.sort if self.sort in names or self.sort in [l for _, l in self.groups] else ""
            if not sort and not by_time:
                sort = names[0]
            if sort:
                rows.sort(key=lambda x: (x.get(sort) is None, x.get(sort) if not isinstance(x.get(sort), str) else 0,
                                         str(x.get(sort))), reverse=False)
                if self.sort_desc and not (by_time and not self.sort):
                    rows = [r for r in rows if r.get(sort) is not None][::-1] + [r for r in rows if r.get(sort) is None]
            else:
                rows.sort(key=lambda x: str(list(x.values())[0]))
            if self.q.get("percent"):
                for op, _, n in self.measures:
                    if op in ("sum", "count"):
                        tot = sum(r[n] or 0 for r in rows)
                        for r in rows:
                            r[f"{n} %"] = round(100 * (r[n] or 0) / tot, 2) if tot else None
            out["groups"] = len(rows)
            for kf, _ in self.groups:
                if isinstance(kf, _NameKey) and (alike := kf.look_alikes()):
                    out["names_that_may_be_the_same"] = alike
                    out["names_note"] = "kept apart in the totals: they may or may not be one party. Show them to the owner and let them decide."
            out["rows"] = rows[: self.limit]
            if len(rows) > self.limit:
                out["shown"] = f"first {self.limit} of {len(rows)} groups (sorted by {sort or 'name'}); raise limit to see more"
            out["all_groups"] = {n: self._final(op, self._merge(op, [st[i] for st in self.acc.values()]))
                                 for i, (op, _, n) in enumerate(self.measures)}
        if self.list_rows:
            items = sorted(self.sample, key=lambda t: (-t[0], t[1])) if self.sort_fn is not None else self.sample
            out["matching_rows"] = [d for _, _, d in items]
            if self.matched > len(items):
                out["matching_rows_note"] = f"showing {len(items)} of {self.matched}"
        return out

    @staticmethod
    def _merge(op, states):
        m = [0, 0, None, None, set() if op == "count_distinct" else []]
        for s in states:
            m[0] += s[0]
            m[1] += s[1]
            m[2] = s[2] if m[2] is None or (s[2] is not None and s[2] < m[2]) else m[2]
            m[3] = s[3] if m[3] is None or (s[3] is not None and s[3] > m[3]) else m[3]
            if op == "count_distinct":
                m[4] |= s[4]
            elif op == "median":
                m[4].extend(s[4])
        return m

    def wanted_cols(self):
        return [c for c, _ in self.columns]


BIG_SHEET_XML = 24_000_000     # a sheet whose XML is this big holds ~60,000 rows of a typical table


def _sheet_xml_bytes(path: str) -> int:
    try:
        with zipfile.ZipFile(path) as z:
            return max((i.file_size for i in z.infolist() if i.filename.startswith("xl/worksheets/") and i.filename.endswith(".xml")),
                       default=0)
    except zipfile.BadZipFile:
        return 0


def is_big(path: str) -> bool:
    """Too big to hold in memory cell by cell. By row count, and by the size of the sheet's XML as well: a file written
    in streaming mode (by this tool, pandas to_excel in some modes, exports) does not say how many rows it has."""
    low = path.lower()
    if low.endswith((".xlsx", ".xlsm")):
        if _sheet_xml_bytes(path) > BIG_SHEET_XML:
            return True
        from openpyxl import load_workbook
        probe = load_workbook(path, read_only=True)
        biggest = max(((ws.max_row or 0) for ws in probe.worksheets), default=0)
        probe.close()
        return biggest > BIG_ROWS
    if low.endswith((".csv", ".tsv", ".txt")):
        with open(path, "rb") as f:
            return sum(chunk.count(b"\n") for chunk in iter(lambda: f.read(1 << 20), b"")) > BIG_ROWS
    return False


def _streams(path: str, values: bool = True):
    """(sheet title, iterator of row tuples) for each sheet, read once without holding the file in memory. values=True gives
    what formulas last showed (Excel's saved results); False gives the formulas as written."""
    low = path.lower()
    if low.endswith((".csv", ".tsv", ".txt")):
        text = open(path, encoding="utf-8-sig", errors="replace", newline="")
        try:
            sample = text.read(4096)
            text.seek(0)
            try:
                delim = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
            except csv.Error:
                delim = ","
            yield "csv", (tuple(_csv_cell(v) for v in row) for row in csv.reader(text, delimiter=delim))
        finally:
            text.close()
        return
    from openpyxl import load_workbook
    wb = load_workbook(path, read_only=True, data_only=values)
    try:
        for ws in wb.worksheets:
            yield ws.title, ws.iter_rows(values_only=True)
    finally:
        wb.close()                  # also when the caller stops reading early


def _header_of(rows_iter):
    """Read up to 30 rows to find the header (the first row with the most text cells); returns (header_row, headers,
    the rows read so far as (r, vals), the rest of the iterator)."""
    head, hdr, best = [], 1, 0
    for r, vals in enumerate(rows_iter, 1):
        head.append((r, vals))
        texts = sum(1 for v in vals if isinstance(v, str) and v.strip() and not is_formula(v))
        if texts > best and texts >= 2:
            best, hdr = texts, r
        if r >= 30:
            break
    hv = next((v for r, v in head if r == hdr), ())
    headers = {c: str(v).strip() for c, v in enumerate(hv, 1) if v not in (None, "")}
    return hdr, headers, head, rows_iter


def _dayfirst(path: str, cols_text) -> bool:
    """Is '03/05/2011' 3 May or 5 March in this file? Looked up from the file itself: a first part over 12 means day first,
    a second part over 12 means month first. Indian files default to day first."""
    first = second = 0
    seen = 0
    for _, it in _streams(path):
        for vals in it:
            for v in vals:
                if isinstance(v, str) and (m := _DATE_RE.match(v)) and len(m.group(1)) <= 2:
                    seen += 1
                    first += int(m.group(1)) > 12
                    second += int(m.group(2)) > 12
            if seen > 200_000 or (first + second) > 50:
                break
        break
    return not (second > first)


def query(path: str, queries: list, sheet: str | None = None, include_rows_below_totals: bool = False) -> dict:
    """Run filters / groups / measures over every entry row. Total rows (and the block under them) are never counted as
    entries, whatever their size; formulas count with the value they show."""
    if isinstance(queries, dict):
        queries = [queries]
    if not isinstance(queries, list) or not queries or len(queries) > 8:
        return {"error": "queries: 1 to 8 objects like {'where': [...], 'group_by': 'Country', 'measures': [{'op': 'sum', 'of': 'Amount'}]}"}
    dayfirst = _dayfirst(path, None) if path.lower().endswith((".csv", ".tsv", ".txt")) else True
    big = is_big(path)
    out = {"file": Path(path).name}
    if not big:
        book = load(path)
        cand = [sh for sh in book["sheets"] if not sheet or norm(sh.title) == norm(sheet)]
        if not cand:
            return {"error": f"no sheet '{sheet}'. Sheets: {', '.join(s.title for s in book['sheets'])}"}
        if len(cand) > 1:
            fits = []
            for sh in cand:
                try:
                    [_Query(q, sh, dayfirst) for q in queries]
                    fits.append(sh)
                except ValueError:
                    pass
            if len(fits) != 1:
                return {"error": f"the file has {len(cand)} sheets ({', '.join(s.title for s in cand)}); say which sheet"}
            cand = fits
        sh = cand[0]
        try:
            qs = [_Query(q, sh, dayfirst) for q in queries]
        except ValueError as e:
            return {"error": str(e)}
        rows = [r for r in sh.data_rows() if sh.has_content(r)]
        if include_rows_below_totals:
            rows += [r for r in sh.below if sh.has_content(r)]
        unknown = [0]

        for r in rows:
            def get(c, r=r):
                v = sh.ev.value(r, c)
                if v is UNK:
                    unknown[0] += 1
                    return None
                if v in (None, "") and (g := sh.carried(r, c)) is not None:
                    return g
                return v
            for q in qs:
                q.add(r, get, lambda q=q, r=r, get=get: _row_out(sh.headers, r, get, q.wanted_cols() or list(sh.headers)))
        if groups := sh.group_columns():
            out["filled_down"] = {sh.headers.get(c) or col_letter(c): "written only on the first row of each group in the file; the empty cells below "
                                  "were read as the same value, so a filter or a group on it covers every row of the group" for c in groups}
        out.update({"sheet": sh.title, "entry_rows": len(rows), "not_counted": {
            **({"total_rows": sh.total_rows} if sh.total_rows else {}),
            **({"rows_below_totals": f"{sh.below[0]}-{sh.below[-1]}"} if sh.below and not include_rows_below_totals else {})}})
        if not out["not_counted"]:
            out.pop("not_counted")
        if unknown[0]:
            out["unknown_formula_cells"] = f"{unknown[0]} cells hold formulas this tool cannot work out; they count as blank"
    else:
        for title, it in _streams(path):
            if sheet and norm(title) != norm(sheet):
                continue
            hdr, headers, head, rest = _header_of(it)
            hd = _Head(title, headers)
            try:
                qs = [_Query(q, hd, dayfirst) for q in queries]
            except ValueError as e:
                if sheet:
                    return {"error": str(e)}
                continue
            n, totals = 0, []

            def feed(r, vals):
                nonlocal n
                if all(v in (None, "") for v in vals):
                    return
                if any(isinstance(v, str) and TOTAL_RE.match(v.strip()) for v in vals[:6]):
                    totals.append(r)
                    return
                n += 1
                L = len(vals)

                def get(c):
                    return vals[c - 1] if c <= L else None
                for q in qs:
                    q.add(r, get, lambda q=q: _row_out(headers, r, get, q.wanted_cols() or list(headers)))
            for r, vals in head:
                if r > hdr:
                    feed(r, vals)
            for r, vals in enumerate(rest, len(head) + 1):
                feed(r, vals)
            out.update({"sheet": title, "entry_rows": n, "big": True})
            if totals:
                out["not_counted"] = {"total_rows": totals[:5]}
            break
        else:
            return {"error": "no sheet has all the columns these queries name" if not sheet else f"no sheet '{sheet}'"}
    out["answers"] = [q.result() for q in qs]
    out["how_to_use"] = ("Quote these numbers as they are (they are worked out from every row). Say what you filtered on in "
                         "plain words. If not_numbers_skipped is there, say how many rows were left out and why.")
    return out


def _row_out(headers, r, get, cols):
    d = {"row": r}
    for c in cols:
        v = get(c)
        if v not in (None, ""):
            d[headers.get(c) or col_letter(c)] = str(v) if hasattr(v, "isoformat") else short(v)
    return d


# ---------------------------------------------------------------- edits on very big sheets, one row at a time
_REL_REF = re.compile(r"(?<![A-Za-z_!$])(\$?)([A-Z]{1,3})(\$?)(\d{1,7})(?![A-Za-z_(\d])")


def _shift_rows(formula: str, delta: int) -> str:
    """'=D2*E2' written for row 2, as row 2+delta (relative rows move, $-anchored rows stay), without a full parser."""
    if not delta:
        return formula
    parts = re.split(r'("[^"]*")', formula)               # leave text in quotes alone
    return "".join(p if p.startswith('"') else _REL_REF.sub(
        lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}{m.group(4) if m.group(3) else int(m.group(4)) + delta}", p)
        for p in parts)


def _row_template(head, formula) -> str:
    """'={Quantity}*{UnitPrice}' -> '=D{r}*F{r}' ({r} is filled in per row); a plain formula is kept as written."""
    def rep(m):
        if m.group(1) == "r":
            return "{r}"
        c, err = head.find_column(m.group(1))
        if err:
            raise ValueError(err)
        return f"{col_letter(c)}{{r}}"
    return re.sub(r"\{([^{}]+)\}", rep, "=" + str(formula).lstrip("="))


def _guard_column(i, header, letter, action, owner_words):
    if owner_words and not named_by_owner(header, letter, owner_words):
        raise ValueError(f"change {i + 1}: the owner did not name the column {letter} '{header}'. Change only the columns they "
                         "named; if you think this one must change too, ask_owner first")
    if action == "clear" and owner_words and not CLEAR_WORDS.search(owner_words):
        raise ValueError(f"change {i + 1}: the owner did not ask to empty or remove anything; do not clear '{header}'")


def _has_formulas(path: str, probe_rows: int = 2000) -> bool:
    """Does the start of the sheet hold formulas? A big data dump almost never does; then values and formulas are the same
    thing and the file need only be read once."""
    for _, it in _streams(path, values=False):
        for r, vals in enumerate(it, 1):
            if any(is_formula(v) for v in vals):
                return True
            if r >= probe_rows:
                break
    return False


def apply_big(src: str, dst: str, changes: list[dict], preview: bool = False, owner_words: str = "") -> dict:
    """The same edit spec on a sheet too big to hold in memory: read as a stream, written as a stream, then read back and
    compared cell by cell. Rows are picked with 'data', 'A-B' or {'where': [...]} (names lists: use where 'in')."""
    low = src.lower()
    is_csv = low.endswith((".csv", ".tsv", ".txt"))
    dayfirst = _dayfirst(src, None) if is_csv else True
    reports, first_error = [], None
    title_of_target = None
    formulas = False if is_csv else _has_formulas(src)
    # first pass: only the header, to resolve columns (and choose the sheet)
    for title, it in _streams(src, values=False):
        hdr, headers, _, _ = _header_of(it)
        hd = _Head(title, dict(headers))
        try:
            steps = []
            for i, ch in enumerate(changes):
                action = ch.get("action", "set")
                if action == "add_column":
                    header = str(ch.get("header") or ch.get("name") or ch.get("column") or "").strip()
                    if not header:
                        raise ValueError(f"change {i + 1}: add_column needs header")
                    c = hd.max_col + 1
                    hd.headers[c] = header
                    hd.max_col = c
                else:
                    c, err = hd.find_column(ch.get("column"))
                    if err:
                        raise ValueError(f"change {i + 1}: {err}")
                    added = any(s["col"] == c and s["action"] == "add_column" for s in steps)
                    if not added:
                        _guard_column(i, hd.headers.get(c, ""), col_letter(c), action, owner_words)
                rows = ch.get("rows", "data")
                where, span = [], None
                if isinstance(rows, dict):
                    if rows.get("names"):
                        raise ValueError(f"change {i + 1}: on a sheet this big pick rows with where, e.g. "
                                         "{'where': [{'column': 'CustomerID', 'op': 'in', 'value': [...]}]}")
                    w = rows.get("where") or []
                    where = [_cond_fn(x, hd, dayfirst) for x in (w if isinstance(w, list) else [w])]
                elif isinstance(rows, str) and (m := re.fullmatch(r"\s*(\d+)\s*-\s*(\d+)\s*", rows)):
                    span = (int(m.group(1)), int(m.group(2)))
                elif isinstance(rows, list):
                    span = set(int(x) for x in rows)
                elif rows not in ("data", "all", None):
                    raise ValueError(f"change {i + 1}: rows must be 'data', 'A-B', a list of row numbers or {{'where': [...]}}")
                kind = next((k for k in ("copy_from", "compute", "formula", "value") if k in ch), None)
                if action != "clear" and not kind:
                    raise ValueError(f"change {i + 1}: give value, formula, compute or copy_from")
                fn = None
                if kind == "copy_from":
                    fn = _value_fn(ch["copy_from"], hd)[0]
                elif kind == "compute":
                    fn = _value_fn(ch["compute"] if "{" in str(ch["compute"]) else "{" + str(ch["compute"]) + "}", hd)[0]
                formula = _row_template(hd, ch["formula"]) if kind == "formula" else None
                steps.append({"i": i, "col": c, "action": action, "where": where, "span": span, "kind": kind, "fn": fn,
                              "formula": formula, "value": ch.get("value"), "only_empty": bool(ch.get("only_empty")),
                              "include_total_rows": bool(ch.get("include_total_rows")), "header": hd.headers.get(c, ""),
                              "n": 0, "examples": [], "over": 0})
        except ValueError as e:
            first_error = first_error or str(e)
            continue
        title_of_target, target_hdr = title, hdr
        break
    if title_of_target is None:
        return {"error": first_error or "no sheet has the columns these changes name", "nothing_changed": True}

    planned = {}
    out_rows_written = 0

    def new_rows(title, vals_it, form_it):
        """Yield the rows of the edited sheet, recording every planned cell."""
        nonlocal out_rows_written
        pairs = zip(vals_it, form_it) if form_it is not None else ((v, v) for v in vals_it)
        for r, (vals, forms) in enumerate(pairs, 1):
            row = list(forms)
            if title == title_of_target:
                if r == target_hdr:
                    for s in steps:
                        if s["action"] == "add_column":
                            while len(row) < s["col"]:
                                row.append(None)
                            row[s["col"] - 1] = s["header"]
                            planned[(r, s["col"])] = s["header"]
                elif r > target_hdr and not all(v in (None, "") for v in vals):
                    total = any(isinstance(v, str) and TOTAL_RE.match(v.strip()) for v in vals[:6])
                    L = len(vals)

                    def get(c, vals=vals, L=L):
                        return vals[c - 1] if c <= L else None
                    for s in steps:
                        if total and not s["include_total_rows"]:
                            continue
                        if s["span"] is not None and (r not in s["span"] if isinstance(s["span"], set)
                                                      else not s["span"][0] <= r <= s["span"][1]):
                            continue
                        if s["where"] and not all(ok(get) for ok in s["where"]):
                            continue
                        c = s["col"]
                        old = row[c - 1] if c <= len(row) else None
                        if s["only_empty"] and old not in (None, ""):
                            continue
                        if s["action"] == "clear":
                            new = None
                        elif s["kind"] == "value":
                            new = s["value"]
                        elif s["kind"] == "formula":
                            new = s["formula"].replace("{r}", str(r)) if "{r}" in s["formula"] else \
                                _shift_rows(s["formula"], r - (target_hdr + 1))
                        else:
                            new = s["fn"](get)
                            if isinstance(new, float):
                                new = round(new, 10)
                                if new.is_integer():
                                    new = int(new)
                        if new == old:
                            continue
                        while len(row) < c:
                            row.append(None)
                        row[c - 1] = new
                        planned[(r, c)] = new
                        s["n"] += 1
                        s["over"] += old not in (None, "")
                        if len(s["examples"]) < 5:
                            s["examples"].append(f"{col_letter(c)}{r}: {short(old) if old not in (None, '') else 'blank'} -> "
                                                 f"{short(new) if new not in (None, '') else 'blank'}")
            out_rows_written += 1
            yield row

    import time as _time
    t0 = _time.time()
    if is_csv:
        stream = _streams(src, values=False)          # kept referenced: a stream is closed the moment nothing holds it
        _, vit = next(stream)
        rows_iter = new_rows("csv", vit, None)
        if preview:
            for _ in rows_iter:
                pass
            stream.close()
        else:
            with open(src, encoding="utf-8-sig", errors="replace", newline="") as f:
                sample = f.read(4096)
            try:
                delim = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
            except csv.Error:
                delim = ","
            with open(dst, "w", encoding="utf-8-sig", newline="") as f:
                w = csv.writer(f, delimiter=delim, lineterminator="\r\n")
                for row in rows_iter:
                    w.writerow(["" if v is None else v for v in row])
            stream.close()
    else:
        from openpyxl import Workbook
        wbo = Workbook(write_only=True) if not preview else None
        pairs = zip(_streams(src, values=True), _streams(src, values=False)) if formulas else             (((t, v), (t, None)) for t, v in _streams(src, values=False))
        for (title, vit), (_, fit) in pairs:
            ws = wbo.create_sheet(title) if wbo else None
            for row in new_rows(title, vit, fit):
                if ws is not None:
                    ws.append(row)
        if wbo:
            wbo.save(dst)
    for s in steps:
        rep = {"change": s["i"] + 1, "sheet": title_of_target, "action": s["action"],
               "column": f"{col_letter(s['col'])} '{s['header']}'" + (" (new)" if s["action"] == "add_column" else ""),
               "rows_changed": s["n"] if s["action"] != "add_column" or s["n"] else s["n"], "examples": s["examples"]}
        if s["over"]:
            rep["values_replaced"] = s["over"]
        if not s["n"] and s["action"] != "add_column":
            rep["warning"] = "no row matched; nothing changed for this step"
        reports.append(rep)
    result = {"changes": reports, "cells_to_change": len(planned), "big": True,
              "note": "a big file: edited row by row. Formatting (colours, widths) is not carried over at this size; values "
                      "and formulas are. Formulas that read the changed cells were not re-worked out here."}
    if preview:
        return {**result, "preview": True, "next": "Nothing is saved yet. Save it by calling again without preview."}
    if not planned:
        return {**result, "saved": False, "note": "nothing to change: the cells already hold these values"}
    # read back: every cell equals the original, except the planned ones, which equal the plan
    wrong, outside = [], []
    src_it, dst_it = _streams(src, values=False), _streams(dst, values=False)
    for (ta, ia), (tb, ib) in zip(src_it, dst_it):
        import itertools
        for r, (va, vb) in enumerate(itertools.zip_longest(ia, ib, fillvalue=()), 1):
            n = max(len(va), len(vb))
            for c in range(1, n + 1):
                a = va[c - 1] if c <= len(va) else None
                b = vb[c - 1] if c <= len(vb) else None
                if ta == title_of_target and (r, c) in planned:
                    exp = planned[(r, c)]
                    if not (b == exp or (exp in (None, "") and b in (None, "")) or str(b) == str(exp)):
                        if len(wrong) < 10:
                            wrong.append(f"{col_letter(c)}{r}")
                elif a != b and not (a in (None, "") and b in (None, "")) and str(a) != str(b):
                    if len(outside) < 10:
                        outside.append(f"{ta}!{col_letter(c)}{r}: {short(a)} -> {short(b)}")
    result.update({"saved": True, "verified": not wrong and not outside, "seconds": round(_time.time() - t0, 1),
                   "check": "read the saved file back: only the cells listed above changed" if not wrong and not outside
                   else "MISMATCH after saving: do not send this file",
                   **({"changed_outside_plan": outside} if outside else {}), **({"not_written": wrong} if wrong else {})})
    return result


def entry_count(path: str) -> int:
    """How many rows of the first sheet hold something (header and totals included; blank rows do not count). Streamed for a
    big file, so 500,000 rows cost memory for one row."""
    if is_big(path):
        gen = _streams(path, values=False)
        try:
            for _, it in gen:
                return sum(1 for vals in it if any(v not in (None, "") for v in vals))
            return 0
        finally:
            gen.close()
    sh = load(path, big_ok=False)["sheets"][0]
    return len({r for (r, _), v in sh.cells.items() if v not in (None, "")})


def entry_counts(path: str) -> list[tuple[str, list, int]]:
    """[(sheet title, header titles, rows holding something)] for every sheet; streamed for a big file."""
    out = []
    if is_big(path):
        gen = _streams(path, values=False)
        try:
            for title, it in gen:
                _, headers, head_rows, rest = _header_of(it)
                n = sum(1 for _, vals in head_rows if any(v not in (None, "") for v in vals)) + sum(1 for vals in rest if any(v not in (None, "") for v in vals))
                out.append((title, [h for _, h in sorted(headers.items()) if h], n))
        finally:
            gen.close()
        return out
    for sh in load(path, big_ok=False)["sheets"]:
        out.append((sh.title, [h for _, h in sorted(sh.headers.items()) if h], len({r for (r, _), v in sh.cells.items() if v not in (None, "")})))
    return out


def main(spec_path: str) -> dict:
    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    try:
        if spec["op"] == "layout":
            return layout(spec["src"], spec.get("sheet"), spec.get("rows"), spec.get("find"))
        if spec["op"] == "query":
            return query(spec["src"], spec.get("queries") or [], spec.get("sheet"), bool(spec.get("include_rows_below_totals")))
        if is_big(spec["src"]):
            return apply_big(spec["src"], spec["dst"], spec.get("changes") or [], bool(spec.get("preview")),
                             spec.get("owner_words") or "")
        return apply(spec["src"], spec["dst"], spec.get("changes") or [], bool(spec.get("preview")), spec.get("owner_words") or "")
    except ValueError as e:
        return {"error": str(e)}
