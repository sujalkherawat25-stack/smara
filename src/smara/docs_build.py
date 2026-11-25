"""Builders for Word, Excel, PDF, PowerPoint and poster files from plain structured content.

They run inside Smara itself (python-docx, openpyxl, python-pptx, reportlab, pypdfium2), so a shop PC needs neither Office
nor Python. Indian conventions are built in: lakh/crore digit grouping and the rupee sign, and Devanagari (Hindi, Marathi)
and other Indian scripts through Windows' own Nirmala UI font. Nothing here touches the network or runs any macro.
"""
from __future__ import annotations

import ast
import math
import re
from pathlib import Path
from xml.sax.saxutils import escape

MAX_BLOCKS = 400
MAX_ROWS = 5000
MAX_COLS = 40
MAX_SLIDES = 60

THEMES = {   # primary, dark, accent, soft background
    "teal": ("#0E5C63", "#083B40", "#E2673B", "#F4F1EA"),
    "blue": ("#1F4E9C", "#12306A", "#F2A900", "#EEF3FB"),
    "orange": ("#D9581F", "#8F3410", "#1F4E9C", "#FFF4EA"),
    "green": ("#1E7B45", "#0F4D2B", "#F2A900", "#EFF7F0"),
    "maroon": ("#8A1C3A", "#561124", "#E8A317", "#FAF1F3"),
    "black": ("#222831", "#0F1216", "#E2A400", "#F3F3F1"),
}


# ------------------------------------------------------------------------------------------------ numbers and text
def inr(value, decimals: int = 0, symbol: bool = False) -> str:
    """12345678.5 -> '1,23,45,678' (lakh and crore grouping); symbol adds the rupee sign."""
    number = float(value)
    negative = number < 0
    text = f"{abs(number):.{decimals}f}"
    whole, _, fraction = text.partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ",".join(parts + [tail])
    out = whole + ("." + fraction if fraction else "")
    return ("-" if negative else "") + ("₹" if symbol else "") + out


def is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def display(value, fmt: str | None = None) -> str:
    """A table cell as text. Numbers follow the column format: inr, rupees, number, percent, or plain."""
    if value is None:
        return ""
    if is_number(value):
        if fmt == "inr":
            return inr(value, 0 if float(value).is_integer() else 2)
        if fmt == "rupees":
            return inr(value, 0 if float(value).is_integer() else 2, symbol=True)
        if fmt == "percent":
            return f"{value * 100:.1f}%" if abs(value) <= 1 else f"{value:.1f}%"
        if fmt == "number":
            return f"{value:,.0f}" if float(value).is_integer() else f"{value:,.2f}"
        return str(int(value)) if float(value).is_integer() else f"{value:.2f}".rstrip("0").rstrip(".")
    return str(value)


def clean_text(value, limit: int = 4000) -> str:
    text = "" if value is None else str(value)
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)[:limit]


def luminance(hex_color: str) -> float:
    r, g, b = (int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    lin = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in (r, g, b)]
    return .2126 * lin[0] + .7152 * lin[1] + .0722 * lin[2]


def contrast(a: str, b: str) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + .05) / (lb + .05)


def text_on(background: str) -> str:
    """White or near-black, whichever reads better on the background."""
    return "#FFFFFF" if contrast("#FFFFFF", background) >= contrast("#16181B", background) else "#16181B"


def readable_accent(color: str, background: str) -> str:
    """The colour itself when it reads on the background (4.5:1), otherwise the better of white and ink."""
    return color if contrast(color, background) >= 4.5 else text_on(background)


def theme_colors(name: str | None) -> tuple[str, str, str, str]:
    return THEMES.get(str(name or "teal").lower(), THEMES["teal"])


def hex_color(value, default: str) -> str:
    return value if isinstance(value, str) and re.fullmatch(r"#[0-9A-Fa-f]{6}", value) else default


# ------------------------------------------------------------------------------------------------ fonts for PDF
_FONTS: dict[str, tuple[str, str]] = {}


def pdf_fonts() -> tuple[str, str]:
    """(regular, bold) font names. Nirmala UI (ships with Windows) covers Latin, Devanagari and the rupee sign."""
    if "f" in _FONTS:
        return _FONTS["f"]
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    names = ("Helvetica", "Helvetica-Bold")
    for path in (Path(r"C:\Windows\Fonts\Nirmala.ttc"), Path("/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf")):
        try:
            if path.is_file():
                if path.suffix.lower() == ".ttc":
                    pdfmetrics.registerFont(TTFont("SmaraSans", str(path), subfontIndex=0))
                    pdfmetrics.registerFont(TTFont("SmaraSans-Bold", str(path), subfontIndex=1))
                else:
                    pdfmetrics.registerFont(TTFont("SmaraSans", str(path)))
                    pdfmetrics.registerFont(TTFont("SmaraSans-Bold", str(path)))
                names = ("SmaraSans", "SmaraSans-Bold")
                break
        except Exception:
            continue
    _FONTS["f"] = names
    return names


def _rows(rows, limit=MAX_ROWS) -> list[list]:
    if not isinstance(rows, list) or len(rows) > limit:
        raise ValueError(f"A table can have at most {limit} rows")
    out = []
    for row in rows:
        if not isinstance(row, list) or len(row) > MAX_COLS:
            raise ValueError(f"Each table row must be a list of at most {MAX_COLS} cells")
        out.append([cell if is_number(cell) else clean_text(cell, 1000) for cell in row])
    return out


# A row is highlighted as a total only if it reads like one: the flag alone once styled the last party in a list as the total.
TOTAL_WORDS = re.compile(r"\b(sub-?total|totals?|net (payable|amount|due|total|profit|loss)|closing balance|balance due|sum)\b"
                         r"|(?<![\u0900-\u097F])(कुल|एकूण)(?![\u0900-\u097F])|કુલ|மொத்தம்|মোট", re.I)


def is_total_row(rows: list) -> bool:
    """The last row says it is a total, or it has a blank label with a figure (an unlabelled total line)."""
    if not rows or not rows[-1]:
        return False
    last = rows[-1]
    if any(not is_number(cell) and TOTAL_WORDS.search(str(cell)) for cell in last):
        return True
    return len(rows) > 1 and not str(last[0]).strip() and any(is_number(cell) for cell in last)


def clean_blocks(blocks) -> list[dict]:
    if not isinstance(blocks, list) or not blocks or len(blocks) > MAX_BLOCKS:
        raise ValueError(f"Give 1 to {MAX_BLOCKS} content blocks")
    out = []
    for block in blocks:
        if not isinstance(block, dict):
            raise ValueError("Each block must be an object with a type")
        kind = str(block.get("type") or "").lower()
        if kind in {"heading", "paragraph"}:
            item = {"type": kind, "text": clean_text(block.get("text")), "level": min(3, max(1, int(block.get("level") or 1))),
                    "bold": bool(block.get("bold")), "align": block.get("align") if block.get("align") in ("left", "center", "right") else "left"}
        elif kind in {"bullets", "numbered"}:
            items = block.get("items")
            if not isinstance(items, list) or len(items) > 200:
                raise ValueError("Bullet lists need an items list")
            item = {"type": kind, "items": [clean_text(v, 1000) for v in items]}
        elif kind == "table":
            header = block.get("header")
            header = [clean_text(v, 200) for v in header] if isinstance(header, list) else []
            fmts = block.get("formats") if isinstance(block.get("formats"), list) else []
            item = {"type": "table", "header": header, "rows": _rows(block.get("rows") or []), "total_row": bool(block.get("total_row")),
                    "formats": [f if f in ("inr", "rupees", "number", "percent", "integer") else None for f in fmts]}
            item["total_row"] = item["total_row"] and is_total_row(item["rows"])
            if not item["header"] and not item["rows"]:
                raise ValueError("A table needs a header or rows")
        elif kind == "image":
            item = {"type": "image", "path": clean_text(block.get("path"), 500), "width_cm": min(18.0, max(2.0, float(block.get("width_cm") or 12)))}
        elif kind in {"page_break", "spacer"}:
            item = {"type": kind}
        else:
            raise ValueError(f"Unknown block type {kind!r}. Use heading, paragraph, bullets, numbered, table, image, page_break or spacer")
        out.append(item)
    return out


# ------------------------------------------------------------------------------------------------ Word
def _shade(cell, color: str) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    props = cell._tc.get_or_add_tcPr()
    shade = OxmlElement("w:shd")
    shade.set(qn("w:val"), "clear")
    shade.set(qn("w:color"), "auto")
    shade.set(qn("w:fill"), color.lstrip("#"))
    props.append(shade)


def _page_number_field(paragraph) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    run = paragraph.add_run()
    for kind, text in (("begin", None), (None, "PAGE"), ("end", None)):
        if kind:
            element = OxmlElement("w:fldChar")
            element.set(qn("w:fldCharType"), kind)
        else:
            element = OxmlElement("w:instrText")
            element.set(qn("xml:space"), "preserve")
            element.text = text
        run._r.append(element)


def build_docx(path: Path, spec: dict, resolve_image) -> dict:
    from docx import Document
    from docx.enum.section import WD_ORIENT
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt, RGBColor
    blocks = clean_blocks(spec.get("blocks"))
    primary, dark, _accent, _soft = theme_colors(spec.get("theme"))
    document = Document()
    section = document.sections[0]
    if str(spec.get("page") or "A4").upper() == "LETTER":
        section.page_width, section.page_height = Cm(21.59), Cm(27.94)
    else:
        section.page_width, section.page_height = Cm(21.0), Cm(29.7)
    if spec.get("landscape"):
        section.orientation = WD_ORIENT.LANDSCAPE
        section.page_width, section.page_height = section.page_height, section.page_width
    for side in ("left_margin", "right_margin", "top_margin", "bottom_margin"):
        setattr(section, side, Cm(2.0))
    normal = document.styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(11)
    normal.element.rPr.rFonts.set(qn("w:cs"), "Nirmala UI")   # Hindi and other Indian scripts
    normal.paragraph_format.space_after = Pt(6)
    for level, size in ((1, 16), (2, 13), (3, 11.5)):
        style = document.styles[f"Heading {level}"]
        style.font.name, style.font.size, style.font.bold = "Calibri", Pt(size), True
        style.font.color.rgb = RGBColor.from_string(primary.lstrip("#"))
        style.element.rPr.rFonts.set(qn("w:cs"), "Nirmala UI")
    title = clean_text(spec.get("title"), 200)
    if title:
        heading = document.add_paragraph()
        run = heading.add_run(title)
        run.bold, run.font.size = True, Pt(22)
        run.font.color.rgb = RGBColor.from_string(dark.lstrip("#"))
        heading.paragraph_format.space_after = Pt(2)
    subtitle = clean_text(spec.get("subtitle"), 300)
    if subtitle:
        sub = document.add_paragraph()
        run = sub.add_run(subtitle)
        run.font.size, run.font.color.rgb = Pt(11.5), RGBColor(0x55, 0x5B, 0x63)
    if spec.get("header_text"):
        document.sections[0].header.paragraphs[0].text = clean_text(spec["header_text"], 200)
    footer = document.sections[0].footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    if spec.get("footer_text"):
        footer.add_run(clean_text(spec["footer_text"], 200) + "   ")
    footer.add_run("Page ")
    _page_number_field(footer)
    align = {"left": WD_ALIGN_PARAGRAPH.LEFT, "center": WD_ALIGN_PARAGRAPH.CENTER, "right": WD_ALIGN_PARAGRAPH.RIGHT}
    for block in blocks:
        kind = block["type"]
        if kind == "heading":
            document.add_heading(block["text"], level=block["level"])
        elif kind == "paragraph":
            paragraph = document.add_paragraph()
            run = paragraph.add_run(block["text"])
            run.bold = block["bold"]
            paragraph.alignment = align[block["align"]]
        elif kind in {"bullets", "numbered"}:
            for entry in block["items"]:
                document.add_paragraph(entry, style="List Bullet" if kind == "bullets" else "List Number")
        elif kind == "table":
            header, rows, fmts = block["header"], block["rows"], block["formats"]
            columns = max([len(header)] + [len(r) for r in rows] or [1])
            table = document.add_table(rows=0, cols=columns)
            table.style = "Table Grid"
            table.alignment = WD_TABLE_ALIGNMENT.CENTER
            if header:
                cells = table.add_row().cells
                for index in range(columns):
                    cells[index].text = ""
                    run = cells[index].paragraphs[0].add_run(header[index] if index < len(header) else "")
                    run.bold = True
                    run.font.color.rgb = RGBColor.from_string(text_on(primary).lstrip("#"))
                    _shade(cells[index], primary)
            for number, row in enumerate(rows):
                cells = table.add_row().cells
                last = block["total_row"] and number == len(rows) - 1
                for index in range(columns):
                    value = row[index] if index < len(row) else ""
                    fmt = fmts[index] if index < len(fmts) else None
                    cells[index].text = ""
                    paragraph = cells[index].paragraphs[0]
                    run = paragraph.add_run(display(value, fmt if is_number(value) else None))
                    run.bold = last
                    if is_number(value):
                        paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
                    if last:
                        _shade(cells[index], "#E7ECEF")
            document.add_paragraph()
        elif kind == "image":
            document.add_picture(str(resolve_image(block["path"])), width=Cm(block["width_cm"]))
        elif kind == "page_break":
            document.add_page_break()
        elif kind == "spacer":
            document.add_paragraph()
    document.save(str(path))
    return {"blocks": len(blocks)}


# ------------------------------------------------------------------------------------------------ Excel and its formula check
_FORMAT_CODES = {
    "inr": r"[>=10000000]##\,##\,##\,##0;[>=100000]##\,##\,##0;##,##0",
    "rupees": r'[>=10000000]"₹"##\,##\,##\,##0;[>=100000]"₹"##\,##\,##0;"₹"##,##0',
    "number": "#,##0.00", "percent": "0.0%", "date": "dd-mmm-yyyy", "text": "@", "integer": "0",
}
_CELL = re.compile(r"^\$?([A-Za-z]{1,3})\$?(\d{1,7})$")


def column_number(letters: str) -> int:
    value = 0
    for char in letters.upper():
        value = value * 26 + ord(char) - 64
    return value


def column_letter(number: int) -> str:
    out = ""
    while number:
        number, rest = divmod(number - 1, 26)
        out = chr(65 + rest) + out
    return out


class Unverifiable(Exception):
    pass


class Grid:
    """Values and formulas of one sheet, with a small safe evaluator for the formulas people write by hand."""
    FUNCTIONS = {"SUM", "AVERAGE", "MIN", "MAX", "COUNT", "ROUND", "ABS"}

    def __init__(self, cells: dict[str, object]):
        self.cells, self.memo, self.active = cells, {}, set()

    def value(self, ref: str):
        ref = ref.replace("$", "").upper()
        if ref in self.memo:
            return self.memo[ref]
        raw = self.cells.get(ref)
        if isinstance(raw, str) and raw.startswith("="):
            if ref in self.active:
                raise Unverifiable("circular reference")
            self.active.add(ref)
            try:
                self.memo[ref] = self.evaluate(raw[1:])
            finally:
                self.active.discard(ref)
            return self.memo[ref]
        return raw

    def numbers(self, start: str, end: str) -> list[float]:
        a, b = _CELL.match(start), _CELL.match(end)
        if not a or not b:
            raise Unverifiable("bad range")
        out = []
        for column in range(min(column_number(a[1]), column_number(b[1])), max(column_number(a[1]), column_number(b[1])) + 1):
            for row in range(min(int(a[2]), int(b[2])), max(int(a[2]), int(b[2])) + 1):
                value = self.value(f"{column_letter(column)}{row}")
                if is_number(value):
                    out.append(value)
        return out

    def evaluate(self, formula: str):
        text = formula.strip()
        if "!" in text or "'" in text or '"' in text:
            raise Unverifiable("other sheets or text are not checked")
        text = re.sub(r"(?<![A-Za-z0-9_])(\$?[A-Za-z]{1,3}\$?\d{1,7}):(\$?[A-Za-z]{1,3}\$?\d{1,7})(?![A-Za-z0-9_(])",
                      lambda m: f'_range("{m[1]}","{m[2]}")', text)
        text = re.sub(r"(?<![A-Za-z0-9_\"])(\$?[A-Za-z]{1,3}\$?\d{1,7})(?![A-Za-z0-9_(\"])", lambda m: f'_cell("{m[1]}")', text)
        text = text.replace("^", "**")
        try:
            tree = ast.parse(text, mode="eval")
        except SyntaxError as exc:
            raise Unverifiable("formula not understood") from exc
        return self._eval(tree.body)

    def _eval(self, node):
        if isinstance(node, ast.Constant) and is_number(node.value):
            return node.value
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow)):
            left, right = self._eval(node.left), self._eval(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                if right == 0:
                    raise Unverifiable("division by zero")
                return left / right
            if abs(right) > 10:
                raise Unverifiable("power too large")
            return left ** right
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            value = self._eval(node.operand)
            return -value if isinstance(node.op, ast.USub) else value
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            name = node.func.id
            if name == "_cell" and node.args and isinstance(node.args[0], ast.Constant):
                value = self.value(node.args[0].value)
                if value is None:
                    return 0
                if not is_number(value):
                    raise Unverifiable("a referenced cell is not a number")
                return value
            if name == "_range" and len(node.args) == 2:
                return self.numbers(node.args[0].value, node.args[1].value)
            upper = name.upper()
            if upper in self.FUNCTIONS:
                args = [self._eval(a) for a in node.args]
                flat = [v for a in args for v in (a if isinstance(a, list) else [a])]
                if upper == "SUM":
                    return sum(flat)
                if upper == "COUNT":
                    return len(flat)
                if upper == "ABS" and len(flat) == 1:
                    return abs(flat[0])
                if upper == "ROUND" and len(args) in (1, 2):
                    digits = int(args[1]) if len(args) == 2 else 0
                    factor = 10 ** digits
                    return math.floor(abs(args[0]) * factor + .5) / factor * (1 if args[0] >= 0 else -1)
                if upper in {"AVERAGE", "MIN", "MAX"} and flat:
                    return {"AVERAGE": lambda v: sum(v) / len(v), "MIN": min, "MAX": max}[upper](flat)
        raise Unverifiable("formula not understood")


def _sheet_title(name: str, used: set[str]) -> str:
    base = re.sub(r"[\[\]\:\*\?/\\]", " ", clean_text(name, 60)).strip() or "Sheet"
    title, count = base[:31], 2
    while title.lower() in used:
        title = f"{base[:28]} {count}"
        count += 1
    used.add(title.lower())
    return title


def build_xlsx(path: Path, spec: dict) -> dict:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    sheets = spec.get("sheets")
    if not isinstance(sheets, list) or not sheets or len(sheets) > 12:
        raise ValueError("Give 1 to 12 sheets")
    primary, _dark, _accent, _soft = theme_colors(spec.get("theme"))
    book = Workbook()
    book.remove(book.active)
    thin = Side(style="thin", color="C9CED3")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    used: set[str] = set()
    report = []
    for entry in sheets:
        if not isinstance(entry, dict):
            raise ValueError("Each sheet must be an object")
        sheet = book.create_sheet(_sheet_title(entry.get("name") or "Sheet", used))
        header = [clean_text(v, 200) for v in entry.get("header") or []]
        rows = _rows(entry.get("rows") or [])
        columns = max([len(header)] + [len(r) for r in rows] or [1])
        cells: dict[str, object] = {}
        formats = entry.get("formats") if isinstance(entry.get("formats"), dict) else {}
        if header:
            sheet.append(header)
            for index in range(len(header)):
                cell = sheet.cell(row=1, column=index + 1)
                cell.font = Font(bold=True, color=text_on(primary).lstrip("#"))
                cell.fill = PatternFill("solid", fgColor=primary.lstrip("#"))
                cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
                cell.border = border
        for row in rows:
            sheet.append(row)
        first = 2 if header else 1
        totals = entry.get("totals") if isinstance(entry.get("totals"), dict) else None
        if totals and rows:
            last = first + len(rows) - 1
            total_row = last + 1
            func = str(totals.get("function") or "SUM").upper()
            if func not in {"SUM", "AVERAGE", "MIN", "MAX", "COUNT"}:
                raise ValueError("Totals function must be SUM, AVERAGE, MIN, MAX or COUNT")
            sheet.cell(row=total_row, column=1, value=clean_text(totals.get("label") or "Total", 60))
            for letter in totals.get("columns") or []:
                if not re.fullmatch(r"[A-Za-z]{1,3}", str(letter)) or column_number(str(letter)) > columns:
                    raise ValueError("Totals columns must be column letters like B or C")
                sheet[f"{str(letter).upper()}{total_row}"] = f"={func}({str(letter).upper()}{first}:{str(letter).upper()}{last})"
            for index in range(1, columns + 1):
                cell = sheet.cell(row=total_row, column=index)
                cell.font = Font(bold=True)
                cell.fill = PatternFill("solid", fgColor="E7ECEF")
        for row in sheet.iter_rows():
            for cell in row:
                if cell.value is not None:
                    cells[cell.coordinate] = cell.value
                    if cell.row >= first:
                        cell.border = border
        for letter, fmt in formats.items():
            if not re.fullmatch(r"[A-Za-z]{1,3}", str(letter)) or fmt not in _FORMAT_CODES:
                raise ValueError("Formats map a column letter to inr, rupees, number, percent, date, text or integer")
            for cell in sheet[str(letter).upper()][first - 1:]:
                if isinstance(cell.value, (int, float)) or (isinstance(cell.value, str) and cell.value.startswith("=")):
                    cell.number_format = _FORMAT_CODES[fmt]
        widths = entry.get("widths") if isinstance(entry.get("widths"), list) else []
        for index in range(1, columns + 1):
            letter = column_letter(index)
            if index <= len(widths) and is_number(widths[index - 1]):
                sheet.column_dimensions[letter].width = min(80, max(4, float(widths[index - 1])))
            else:
                longest = max((len(display(sheet.cell(row=r, column=index).value)) for r in range(1, min(sheet.max_row, 200) + 1)), default=8)
                sheet.column_dimensions[letter].width = min(60, max(10, longest + 3))
        if entry.get("freeze_header", True) and header:
            sheet.freeze_panes = "A2"
        if entry.get("filter") and header and rows:
            sheet.auto_filter.ref = f"A1:{column_letter(columns)}{first + len(rows) - 1}"
        grid, computed, unverified = Grid(cells), {}, []
        for ref, raw in list(cells.items()):
            if isinstance(raw, str) and raw.startswith("="):
                try:
                    computed[ref] = round(grid.value(ref), 6)
                except Unverifiable as exc:
                    unverified.append(f"{ref}: {exc}")
                except (OverflowError, ValueError, ZeroDivisionError):
                    unverified.append(f"{ref}: could not be calculated")
        report.append({"sheet": sheet.title, "rows": len(rows), "computed": dict(list(computed.items())[:30]), "unverified": unverified[:10]})
    book.save(str(path))
    return {"sheets": report}


# ------------------------------------------------------------------------------------------------ PDF report
def build_pdf_report(path: Path, spec: dict, resolve_image) -> dict:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT
    from reportlab.lib.pagesizes import A4, letter, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import Image, ListFlowable, ListItem, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    blocks = clean_blocks(spec.get("blocks"))
    primary, dark, _accent, _soft = theme_colors(spec.get("theme"))
    regular, bold = pdf_fonts()
    size = letter if str(spec.get("page") or "A4").upper() == "LETTER" else A4
    if spec.get("landscape"):
        size = landscape(size)
    base = ParagraphStyle("body", fontName=regular, fontSize=10.5, leading=15, spaceAfter=6, textColor=colors.HexColor("#16181B"))
    styles = {1: ParagraphStyle("h1", parent=base, fontName=bold, fontSize=16, leading=20, spaceBefore=10, textColor=colors.HexColor(primary)),
              2: ParagraphStyle("h2", parent=base, fontName=bold, fontSize=13, leading=17, spaceBefore=8, textColor=colors.HexColor(primary)),
              3: ParagraphStyle("h3", parent=base, fontName=bold, fontSize=11.5, leading=15, spaceBefore=6, textColor=colors.HexColor(primary))}
    story = []
    title = clean_text(spec.get("title"), 200)
    if title:
        story.append(Paragraph(escape(title), ParagraphStyle("title", parent=base, fontName=bold, fontSize=22, leading=27, textColor=colors.HexColor(dark), spaceAfter=3)))
    if spec.get("subtitle"):
        story.append(Paragraph(escape(clean_text(spec["subtitle"], 300)), ParagraphStyle("sub", parent=base, fontSize=11.5, textColor=colors.HexColor("#555B63"), spaceAfter=10)))
    for block in blocks:
        kind = block["type"]
        if kind == "heading":
            story.append(Paragraph(escape(block["text"]), styles[block["level"]]))
        elif kind == "paragraph":
            style = ParagraphStyle("p", parent=base, fontName=bold if block["bold"] else regular,
                                   alignment={"left": 0, "center": TA_CENTER, "right": TA_RIGHT}[block["align"]])
            story.append(Paragraph(escape(block["text"]).replace("\n", "<br/>"), style))
        elif kind in {"bullets", "numbered"}:
            story.append(ListFlowable([ListItem(Paragraph(escape(entry), base)) for entry in block["items"]],
                                      bulletType="bullet" if kind == "bullets" else "1", leftIndent=16, bulletFontName=regular))
        elif kind == "table":
            header, rows, fmts = block["header"], block["rows"], block["formats"]
            cell = ParagraphStyle("cell", parent=base, fontSize=9.5, leading=12.5, spaceAfter=0)
            right = ParagraphStyle("cellr", parent=cell, alignment=TA_RIGHT)
            head = ParagraphStyle("head", parent=cell, fontName=bold, textColor=colors.HexColor(text_on(primary)))
            columns = max([len(header)] + [len(r) for r in rows] or [1])
            data = []
            if header:
                data.append([Paragraph(escape(header[i] if i < len(header) else ""), head) for i in range(columns)])
            for number, row in enumerate(rows):
                last = block["total_row"] and number == len(rows) - 1
                line = []
                for i in range(columns):
                    value = row[i] if i < len(row) else ""
                    text = escape(display(value, (fmts[i] if i < len(fmts) else None) if is_number(value) else None))
                    line.append(Paragraph(f"<b>{text}</b>" if last else text, right if is_number(value) else cell))
                data.append(line)
            table = Table(data, repeatRows=1 if header else 0, hAlign="LEFT")
            commands = [("GRID", (0, 0), (-1, -1), .5, colors.HexColor("#C9CED3")), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]
            if header:
                commands.append(("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(primary)))
            if block["total_row"] and rows:
                commands.append(("BACKGROUND", (0, len(data) - 1), (-1, len(data) - 1), colors.HexColor("#E7ECEF")))
            table.setStyle(TableStyle(commands))
            story += [table, Spacer(1, 8)]
        elif kind == "image":
            file = resolve_image(block["path"])
            from PIL import Image as PilImage
            with PilImage.open(file) as picture:
                ratio = picture.height / picture.width
            width = block["width_cm"] * cm
            story.append(Image(str(file), width=width, height=width * ratio))
        elif kind == "page_break":
            story.append(PageBreak())
        elif kind == "spacer":
            story.append(Spacer(1, 14))
    footer_text = clean_text(spec.get("footer_text"), 200)

    def decorate(canvas, document):
        canvas.saveState()
        canvas.setFont(regular, 8.5)
        canvas.setFillColor(colors.HexColor("#555B63"))
        canvas.drawCentredString(size[0] / 2, 1.1 * cm, (footer_text + "   " if footer_text else "") + f"Page {document.page}")
        if spec.get("header_text"):
            canvas.drawString(2 * cm, size[1] - 1.1 * cm, clean_text(spec["header_text"], 120))
        canvas.restoreState()

    SimpleDocTemplate(str(path), pagesize=size, leftMargin=2 * cm, rightMargin=2 * cm, topMargin=2 * cm, bottomMargin=2 * cm,
                      title=title or path.stem, author="Smara").build(story, onFirstPage=decorate, onLaterPages=decorate)
    return {"blocks": len(blocks)}


# ------------------------------------------------------------------------------------------------ PowerPoint
def build_pptx(path: Path, spec: dict, resolve_image) -> dict:
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.util import Emu, Inches, Pt
    slides = spec.get("slides")
    if not isinstance(slides, list) or not slides or len(slides) > MAX_SLIDES:
        raise ValueError(f"Give 1 to {MAX_SLIDES} slides")
    primary, dark, accent, soft = theme_colors(spec.get("theme"))
    rgb = lambda value: RGBColor.from_string(value.lstrip("#"))
    deck = Presentation()
    deck.slide_width, deck.slide_height = Inches(13.333), Inches(7.5)
    width, height = deck.slide_width, deck.slide_height

    def style_runs(frame, size, color, bold_text=False):
        for paragraph in frame.paragraphs:
            for run in paragraph.runs:
                run.font.size, run.font.bold, run.font.color.rgb = Pt(size), bold_text, rgb(color)
                run.font.name = "Calibri"

    for number, entry in enumerate(slides):
        if not isinstance(entry, dict):
            raise ValueError("Each slide must be an object")
        title, bullets = clean_text(entry.get("title"), 200), [clean_text(b, 600) for b in (entry.get("bullets") or [])][:14]
        title_slide = number == 0 and not bullets and not entry.get("table") and not entry.get("image")
        slide = deck.slides.add_slide(deck.slide_layouts[6])
        background = slide.background.fill
        background.solid()
        background.fore_color.rgb = rgb(primary if title_slide else "#FFFFFF")
        if title_slide:
            box = slide.shapes.add_textbox(Inches(1), Inches(2.3), width - Inches(2), Inches(2))
            box.text_frame.word_wrap = True
            box.text_frame.text = title
            style_runs(box.text_frame, 44, text_on(primary), True)
            if entry.get("subtitle"):
                sub = slide.shapes.add_textbox(Inches(1), Inches(4.4), width - Inches(2), Inches(1.2))
                sub.text_frame.word_wrap = True
                sub.text_frame.text = clean_text(entry["subtitle"], 300)
                style_runs(sub.text_frame, 22, text_on(primary))
        else:
            band = slide.shapes.add_shape(1, 0, 0, width, Inches(1.25))
            band.fill.solid()
            band.fill.fore_color.rgb = rgb(primary)
            band.line.fill.background()
            heading = slide.shapes.add_textbox(Inches(0.6), Inches(0.2), width - Inches(1.2), Inches(0.9))
            heading.text_frame.word_wrap = True
            heading.text_frame.text = title
            style_runs(heading.text_frame, 30, text_on(primary), True)
            top, has_table, has_image = Inches(1.6), bool(entry.get("table")), bool(entry.get("image"))
            if bullets:
                body = slide.shapes.add_textbox(Inches(0.7), top, (Inches(6.2) if has_image else width - Inches(1.4)), height - top - Inches(0.6))
                frame = body.text_frame
                frame.word_wrap = True
                for index, line in enumerate(bullets):
                    paragraph = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
                    paragraph.text = "•  " + line
                    paragraph.space_after = Pt(10)
                style_runs(frame, 28 if len(bullets) <= 5 else 22, "#16181B")
                top += Inches(0.1) + Inches(0.6) * min(len(bullets), 8)
            if has_table:
                table_spec = entry["table"]
                header = [clean_text(h, 100) for h in table_spec.get("header") or []]
                rows = _rows(table_spec.get("rows") or [], 40)
                columns = max([len(header)] + [len(r) for r in rows] or [1])
                fmts = table_spec.get("formats") if isinstance(table_spec.get("formats"), list) else []
                shape = slide.shapes.add_table(len(rows) + (1 if header else 0), columns, Inches(0.7), min(top, Inches(3.4)),
                                               width - Inches(1.4), Inches(0.5) * (len(rows) + 1))
                table = shape.table
                offset = 0
                if header:
                    for i in range(columns):
                        table.cell(0, i).text = header[i] if i < len(header) else ""
                        table.cell(0, i).fill.solid()
                        table.cell(0, i).fill.fore_color.rgb = rgb(primary)
                        style_runs(table.cell(0, i).text_frame, 18, text_on(primary), True)
                    offset = 1
                for r, row in enumerate(rows):
                    for i in range(columns):
                        value = row[i] if i < len(row) else ""
                        table.cell(r + offset, i).text = display(value, (fmts[i] if i < len(fmts) else None) if is_number(value) else None)
                        style_runs(table.cell(r + offset, i).text_frame, 18, "#16181B")
            if has_image:
                file = resolve_image(clean_text(entry["image"], 500))
                from PIL import Image as PilImage
                with PilImage.open(file) as picture:
                    ratio = picture.height / picture.width
                room_w, room_h = (Inches(5.6) if bullets else width - Inches(1.4)), height - Inches(2.2)
                pic_w = min(room_w, int(room_h / ratio))
                slide.shapes.add_picture(str(file), width - Inches(0.7) - pic_w if bullets else int((width - pic_w) / 2), Inches(1.6), width=pic_w)
        if entry.get("notes"):
            slide.notes_slide.notes_text_frame.text = clean_text(entry["notes"], 3000)
    deck.save(str(path))
    return {"slides": len(slides)}


# ------------------------------------------------------------------------------------------------ poster
POSTER_SIZES = {"A4": (210, 297), "A3": (297, 420), "A5": (148, 210), "square": (200, 200), "story": (150, 267)}


def _fit(canvas, text: str, font: str, width: float, height: float, start: float, floor: float, max_lines: int, leading: float = 1.18):
    """Largest size (down to floor) at which the wrapped text fits the box; returns (size, lines)."""
    from reportlab.lib.utils import simpleSplit
    size = start
    while size >= floor:
        lines = simpleSplit(text, font, size, width)
        if len(lines) <= max_lines and len(lines) * size * leading <= height:
            return size, lines
        size -= 1
    return floor, simpleSplit(text, font, floor, width)[:max_lines]


def build_poster(path: Path, spec: dict, resolve_image) -> dict:
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas as pdfcanvas
    title = clean_text(spec.get("title"), 120)
    if not title:
        raise ValueError("A poster needs a title")
    tagline, highlight = clean_text(spec.get("tagline"), 160), clean_text(spec.get("highlight"), 40)
    details = [clean_text(d, 140) for d in (spec.get("details") or [])][:8]
    cta, footer = clean_text(spec.get("call_to_action"), 120), clean_text(spec.get("footer"), 160)
    primary, dark, accent, soft = theme_colors(spec.get("theme"))
    primary, dark, accent = hex_color(spec.get("primary_color"), primary), hex_color(spec.get("dark_color"), dark), hex_color(spec.get("accent_color"), accent)
    size_name = str(spec.get("size") or "A4")
    size_name = size_name if size_name in POSTER_SIZES else "A4"
    w_mm, h_mm = POSTER_SIZES[size_name]
    if spec.get("landscape") and size_name in {"A4", "A3", "A5"}:
        w_mm, h_mm = h_mm, w_mm
    W, H = w_mm * mm, h_mm * mm
    regular, bold = pdf_fonts()
    c = pdfcanvas.Canvas(str(path), pagesize=(W, H))
    c.setTitle(title)
    c.setAuthor("Smara")
    unit = min(W, H) / 210                # one "millimetre of an A4 page", so spacing scales with the page
    margin = 14 * unit
    inner = W - 2 * margin
    c.setFillColor(soft)
    c.rect(0, 0, W, H, stroke=0, fill=1)
    # Title band
    band_h = H * (0.34 if details or highlight else 0.5)
    c.setFillColor(primary)
    c.rect(0, H - band_h, W, band_h, stroke=0, fill=1)
    c.setFillColor(accent)
    c.rect(0, H - band_h - 4 * unit, W, 4 * unit, stroke=0, fill=1)
    ink = text_on(primary)
    tag_room = (band_h * 0.22) if tagline else 0
    size, lines = _fit(c, title.upper() if title.isascii() else title, bold, inner, band_h - tag_room - 2 * margin, min(W, H) * 0.2, 18, 4)
    c.setFillColor(ink)
    c.setFont(bold, size)
    block_h = len(lines) * size * 1.18
    y = H - (band_h - tag_room) / 2 + block_h / 2 - size
    for line in lines:
        c.drawCentredString(W / 2, y, line)
        y -= size * 1.18
    if tagline:
        t_size, t_lines = _fit(c, tagline, regular, inner, tag_room - 4 * unit, min(W, H) * 0.075, 9, 2)
        c.setFont(regular, t_size)
        c.setFillColor(ink)
        ty = H - band_h + tag_room * 0.55
        for line in t_lines:
            c.drawCentredString(W / 2, ty, line)
            ty -= t_size * 1.2
    cursor = H - band_h - 4 * unit - 12 * unit
    # Highlight badge
    if highlight:
        badge_h = H * 0.11
        badge_w = min(inner, W * 0.8)
        c.setFillColor(accent)
        c.roundRect((W - badge_w) / 2, cursor - badge_h, badge_w, badge_h, 10 * unit, stroke=0, fill=1)
        h_size, h_lines = _fit(c, highlight.upper() if highlight.isascii() else highlight, bold, badge_w - 12 * unit, badge_h - 8 * unit, badge_h * 0.62, 14, 1)
        c.setFillColor(text_on(accent))
        c.setFont(bold, h_size)
        c.drawCentredString(W / 2, cursor - badge_h / 2 - h_size * 0.34, h_lines[0])
        cursor -= badge_h + 12 * unit
    # Call-to-action band at the bottom
    cta_h = H * 0.13 if cta else 0
    foot_h = 12 * unit if footer else 0
    if cta:
        c.setFillColor(dark)
        c.rect(0, foot_h, W, cta_h, stroke=0, fill=1)
        c_size, c_lines = _fit(c, cta, bold, inner, cta_h - 8 * unit, cta_h * 0.5, 12, 2)
        c.setFillColor(text_on(dark))
        c.setFont(bold, c_size)
        cy = foot_h + cta_h / 2 + (len(c_lines) - 1) * c_size * 0.6 - c_size * 0.32
        for line in c_lines:
            c.drawCentredString(W / 2, cy, line)
            cy -= c_size * 1.2
    if footer:
        c.setFillColor(dark if cta else soft)
        c.rect(0, 0, W, foot_h, stroke=0, fill=1)
        f_size, f_lines = _fit(c, footer, regular, inner, foot_h - 2 * unit, 11, 6, 1)
        c.setFillColor(text_on(dark))
        c.setFont(regular, f_size)
        c.drawCentredString(W / 2, foot_h / 2 - f_size * 0.32, f_lines[0])
    # Details as rows with a coloured marker
    floor = foot_h + cta_h + 8 * unit
    if details:
        room = cursor - floor
        row_h = min(room / len(details), H * 0.12)
        d_size = max(10, min(min(W, H) * 0.075, row_h * 0.46))
        y = cursor - row_h / 2
        for line in details:
            fit_size, fit_lines = _fit(c, line, regular, inner - 18 * unit, row_h - 3 * unit, d_size, 9, 2)
            c.setFillColor(accent)
            c.circle(margin + 4 * unit, y, 4 * unit, stroke=0, fill=1)
            c.setFillColor(readable_accent("#16181B", soft))
            c.setFont(regular, fit_size)
            ly = y + (len(fit_lines) - 1) * fit_size * 0.6 - fit_size * 0.32
            for part in fit_lines:
                c.drawString(margin + 14 * unit, ly, part)
                ly -= fit_size * 1.18
            y -= row_h
    c.showPage()
    c.save()
    return {"size": size_name, "details": len(details)}


def render_png(pdf_path: Path, png_path: Path, width_px: int = 1080) -> Path:
    """First page of a PDF as a PNG, for a quick look or for sharing on WhatsApp."""
    import pypdfium2 as pdfium
    document = pdfium.PdfDocument(str(pdf_path))
    try:
        page = document[0]
        scale = width_px / page.get_width()
        page.render(scale=scale).to_pil().convert("RGB").save(str(png_path), optimize=True)
    finally:
        document.close()
    return png_path
