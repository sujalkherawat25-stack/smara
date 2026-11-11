"""Business documents for owners: one block model, rendered to a PDF (print quality, Hindi/Gujarati shaped correctly)
or a Word file. Runs inside the sandbox image, where WeasyPrint, python-docx and the Noto fonts are installed.

A spec is plain JSON the server builds from the ledger (so every number comes from the database, never from the model):
    {"title", "subtitle", "shop": {"name", "line"}, "meta": [[label, value]], "footer",
     "blocks": [heading | paragraph | table | stats | keyvalues | bar_chart | note | letter | signature]}
Money is given in rupees and formatted here in Indian grouping (Rs 1,23,456.50 -> ₹1,23,456.50).
"""
from __future__ import annotations

import html
from decimal import ROUND_HALF_UP, Decimal

ACCENT = "#0b5d4b"
TONES = {"bad": "#b42318", "good": "#067647", "warn": "#b54708", "neutral": "#1f2933"}
BLOCK_TYPES = frozenset({"heading", "paragraph", "table", "stats", "keyvalues", "bar_chart", "note", "letter", "signature"})


def inr(value, decimals: int = 2, symbol: bool = True) -> str:
    """12345678.5 -> ₹1,23,45,678.50 (lakh/crore grouping)."""
    q = Decimal(str(value or 0)).quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP)
    sign = "-" if q < 0 else ""
    whole, _, frac = f"{abs(q):f}".partition(".")
    head, tail = whole[:-3], whole[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    body = ",".join(groups + [tail]) if groups else tail
    return f"{sign}{'₹' if symbol else ''}{body}{('.' + frac) if decimals else ''}"


def _e(text) -> str:
    return html.escape("" if text is None else str(text), quote=True).replace("\n", "<br>")


def _cell(value, kind: str) -> str:
    if value is None or value == "":
        return "–" if kind == "money" else ""
    if kind == "money":
        return inr(value)
    return str(value)


# --------------------------------------------------------------------------------------------------------- HTML / PDF
CSS = f"""
@page {{ size: A4; margin: 16mm 16mm 20mm 16mm;
  @bottom-left {{ content: string(foot); font: 7.5pt 'Noto Sans', sans-serif; color: #7b8794; }}
  @bottom-right {{ content: "Page " counter(page) " of " counter(pages); font: 7.5pt 'Noto Sans', sans-serif; color: #7b8794; }} }}
html {{ font-family: 'Noto Sans', 'Noto Sans Devanagari', 'Noto Sans Gujarati', sans-serif; font-size: 9.5pt; color: #1f2933; line-height: 1.45; }}
body {{ margin: 0; }}
.foot {{ string-set: foot content(); height: 0; overflow: hidden; visibility: hidden; margin: 0; padding: 0; }}
header.top {{ display: table; width: 100%; padding-bottom: 9px; border-bottom: 2.5px solid {ACCENT}; margin-bottom: 16px; }}
header.top .l, header.top .r {{ display: table-cell; vertical-align: bottom; }}
header.top .r {{ text-align: right; }}
.shop {{ font-size: 19pt; font-weight: 700; color: {ACCENT}; letter-spacing: -0.01em; line-height: 1.15; }}
.shopline {{ color: #616e7c; font-size: 8.5pt; margin-top: 2px; }}
.doctitle {{ font-size: 8.5pt; font-weight: 700; letter-spacing: .14em; text-transform: uppercase; color: #616e7c; }}
.subtitle {{ font-size: 13pt; font-weight: 600; margin-top: 2px; }}
table.meta {{ border-collapse: collapse; margin: 0 0 14px; }}
table.meta td {{ padding: 1px 18px 1px 0; font-size: 9pt; }}
table.meta td:first-child {{ color: #616e7c; }}
table.meta td:last-child {{ font-weight: 600; }}
h2 {{ font-size: 11.5pt; color: {ACCENT}; margin: 18px 0 6px; padding-bottom: 3px; border-bottom: 1px solid #d3dcd9; page-break-after: avoid; }}
p {{ margin: 0 0 8px; orphans: 3; widows: 3; }}
div.statwrap {{ margin: 4px -8px 10px; }}
table.stats {{ width: 100%; border-collapse: separate; border-spacing: 8px 0; table-layout: fixed; }}
table.stats td {{ background: #f4f7f6; border: 1px solid #dfe7e4; border-radius: 6px; padding: 8px 10px; vertical-align: top; }}
.stat-l {{ font-size: 7.5pt; text-transform: uppercase; letter-spacing: .07em; color: #616e7c; }}
.stat-v {{ font-size: 15pt; font-weight: 700; margin-top: 1px; line-height: 1.2; }}
.stat-h {{ font-size: 7.5pt; color: #7b8794; }}
table.data {{ width: 100%; border-collapse: collapse; margin: 4px 0 12px; }}
table.data caption {{ caption-side: top; text-align: left; font-weight: 600; padding: 0 0 4px; }}
table.data thead {{ display: table-header-group; }}
table.data th {{ background: {ACCENT}; color: #fff; font-size: 7.8pt; text-transform: uppercase; letter-spacing: .06em; font-weight: 600; padding: 6px 8px; text-align: left; }}
table.data td {{ padding: 5px 8px; border-bottom: 1px solid #e4e9e7; vertical-align: top; }}
table.data tbody tr:nth-child(even) td {{ background: #f8faf9; }}
table.data tr {{ page-break-inside: avoid; }}
table.data .r {{ text-align: right; white-space: nowrap; }}
table.data .c {{ text-align: center; }}
table.data tr.total td {{ font-weight: 700; background: #e6f0ed; border-top: 1.5px solid {ACCENT}; border-bottom: 0; }}
table.data tr.empty td {{ text-align: center; color: #7b8794; padding: 14px; }}
table.kv {{ border-collapse: collapse; margin: 2px 0 10px; }}
table.kv td {{ padding: 2px 22px 2px 0; vertical-align: top; }}
table.kv td:first-child {{ color: #616e7c; }}
table.kv td:last-child {{ font-weight: 600; }}
.chart {{ margin: 4px 0 12px; }}
.chart .t {{ font-weight: 600; margin-bottom: 5px; }}
.bar {{ display: table; width: 100%; margin: 3px 0; }}
.bar > div {{ display: table-cell; vertical-align: middle; }}
.bar .lab {{ width: 26%; font-size: 8.5pt; padding-right: 8px; }}
.bar .trk {{ width: 54%; }}
.bar .trk i {{ display: block; height: 11px; border-radius: 2px; background: {ACCENT}; }}
.bar .val.zero {{ color: #9aa5b1; font-weight: 400; }}
.bar .val {{ width: 20%; text-align: right; font-size: 8.5pt; font-weight: 600; padding-left: 8px; white-space: nowrap; }}
.note {{ background: #f4f7f6; border-left: 3px solid {ACCENT}; padding: 7px 11px; margin: 6px 0 10px; font-size: 8.7pt; color: #3e4c59; }}
.letter {{ font-size: 10pt; line-height: 1.6; margin: 8px 0 12px; }}
.sign {{ margin-top: 34px; width: 46%; border-top: 1px solid #9aa5b1; padding-top: 4px; font-size: 8.5pt; color: #616e7c; }}
"""


def _blocks_html(blocks: list[dict]) -> str:
    out = []
    for b in blocks:
        t = b.get("type")
        if t == "heading":
            out.append(f"<h2>{_e(b.get('text'))}</h2>")
        elif t == "paragraph":
            out.append(f"<p>{_e(b.get('text'))}</p>")
        elif t == "letter":
            out.append(f"<div class='letter'>{_e(b.get('text'))}</div>")
        elif t == "note":
            out.append(f"<div class='note'>{_e(b.get('text'))}</div>")
        elif t == "signature":
            out.append(f"<div class='sign'>{_e(b.get('text') or 'Authorised signatory')}</div>")
        elif t == "keyvalues":
            rows = "".join(f"<tr><td>{_e(k)}</td><td>{_e(v)}</td></tr>" for k, v in b.get("items", []))
            out.append(f"<table class='kv'>{rows}</table>")
        elif t == "stats":
            cells = "".join(
                f"<td><div class='stat-l'>{_e(s.get('label'))}</div>"
                f"<div class='stat-v' style='color:{TONES.get(s.get('tone', 'neutral'), TONES['neutral'])}'>{_e(s.get('value'))}</div>"
                f"{('<div class=stat-h>' + _e(s.get('hint')) + '</div>') if s.get('hint') else ''}</td>"
                for s in b.get("items", []))
            out.append(f"<div class='statwrap'><table class='stats'><tr>{cells}</tr></table></div>")
        elif t == "bar_chart":
            items = b.get("items", [])
            top = max([float(i.get("value") or 0) for i in items] + [1e-9])
            rows = "".join(
                f"<div class='bar'><div class='lab'>{_e(i.get('label'))}</div>"
                f"<div class='trk'>{_bar(i.get('value'), top)}</div>"
                f"<div class='val{' zero' if not float(i.get('value') or 0) else ''}'>{_e(_chart_value(i.get('value'), b.get('money', True)))}</div></div>" for i in items)
            out.append(f"<div class='chart'>{('<div class=t>' + _e(b.get('title')) + '</div>') if b.get('title') else ''}{rows}</div>")
        elif t == "table":
            cols = b.get("columns", [])
            head = "".join(f"<th class='{_align(c)}'>{_e(c.get('label'))}</th>" for c in cols)
            body = "".join("<tr>" + "".join(f"<td class='{_align(c)}'>{_e(_cell(v, c.get('kind', 'text')))}</td>"
                                            for c, v in zip(cols, r)) + "</tr>" for r in b.get("rows", []))
            if not b.get("rows"):
                body = f"<tr class='empty'><td colspan='{len(cols)}'>{_e(b.get('empty') or 'Nothing to show')}</td></tr>"
            total = b.get("total_row")
            foot = ("<tr class='total'>" + "".join(f"<td class='{_align(c)}'>{_e(_cell(v, c.get('kind', 'text')))}</td>"
                                                    for c, v in zip(cols, total)) + "</tr>") if total else ""
            cap = f"<caption>{_e(b.get('caption'))}</caption>" if b.get("caption") else ""
            out.append(f"<table class='data'>{cap}<thead><tr>{head}</tr></thead><tbody>{body}{foot}</tbody></table>")
    return "\n".join(out)


def _bar(value, top: float) -> str:
    v = max(0.0, float(value or 0))
    return f"<i style='width:{v / top * 100:.1f}%'></i>" if v else ""


def _chart_value(value, money: bool) -> str:
    if not float(value or 0):
        return "–"
    return inr(value) if money else str(value)


def _align(col: dict) -> str:
    return {"right": "r", "center": "c"}.get(col.get("align") or ("right" if col.get("kind") == "money" else "left"), "")


def build_html(spec: dict) -> str:
    shop = spec.get("shop") or {}
    meta = "".join(f"<tr><td>{_e(k)}</td><td>{_e(v)}</td></tr>" for k, v in spec.get("meta", []))
    return (f"<!doctype html><html lang='{_e(spec.get('lang') or 'en')}'><head><meta charset='utf-8'><title>{_e(spec.get('title'))}</title>"
            f"<style>{CSS}</style></head><body><div class='foot'>{_e(spec.get('footer') or '')}</div>"
            f"<header class='top'><div class='l'><div class='shop'>{_e(shop.get('name'))}</div>"
            f"<div class='shopline'>{_e(shop.get('line'))}</div></div>"
            f"<div class='r'><div class='doctitle'>{_e(spec.get('title'))}</div>"
            f"<div class='subtitle'>{_e(spec.get('subtitle'))}</div></div></header>"
            f"{('<table class=meta>' + meta + '</table>') if meta else ''}{_blocks_html(spec.get('blocks', []))}</body></html>")


def to_pdf(spec: dict, path: str) -> dict:
    from weasyprint import HTML
    doc = HTML(string=build_html(spec)).render()
    doc.write_pdf(path)
    return {"pages": len(doc.pages)}


# ------------------------------------------------------------------------------------------------------------- Word
def to_docx(spec: dict, path: str) -> dict:
    from docx import Document
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt, RGBColor

    green = RGBColor(0x0B, 0x5D, 0x4B)
    d = Document()
    sec = d.sections[0]
    sec.page_width, sec.page_height = Cm(21), Cm(29.7)
    sec.left_margin = sec.right_margin = Cm(1.8)
    sec.top_margin, sec.bottom_margin = Cm(1.6), Cm(1.8)
    normal = d.styles["Normal"]
    normal.font.name, normal.font.size = "Calibri", Pt(10)
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), "Calibri")

    def shade(cell, fill):
        pr = cell._tc.get_or_add_tcPr()
        el = OxmlElement("w:shd")
        el.set(qn("w:val"), "clear"), el.set(qn("w:color"), "auto"), el.set(qn("w:fill"), fill)
        pr.append(el)

    def run(par, text, bold=False, size=None, color=None):
        r = par.add_run(str(text))
        r.bold = bold
        if size:
            r.font.size = Pt(size)
        if color is not None:
            r.font.color.rgb = color
        return r

    shop = spec.get("shop") or {}
    p = d.add_paragraph()
    run(p, shop.get("name") or "", True, 20, green)
    p.paragraph_format.space_after = Pt(0)
    if shop.get("line"):
        run(d.add_paragraph(), shop["line"], size=9, color=RGBColor(0x61, 0x6E, 0x7C))
    t = d.add_paragraph()
    run(t, (spec.get("title") or "").upper(), True, 9, RGBColor(0x61, 0x6E, 0x7C))
    if spec.get("subtitle"):
        run(t, "   " + spec["subtitle"], True, 13)
    for k, v in spec.get("meta", []):
        m = d.add_paragraph()
        m.paragraph_format.space_after = Pt(0)
        run(m, f"{k}: ", size=9.5, color=RGBColor(0x61, 0x6E, 0x7C))
        run(m, v, True, 9.5)

    def table(rows, widths_align, header=None, total=None, caption=None):
        if caption:
            run(d.add_paragraph(), caption, True)
        n = len(widths_align)
        tb = d.add_table(rows=0, cols=n)
        tb.style = "Table Grid"
        tb.alignment = WD_TABLE_ALIGNMENT.CENTER
        if header:
            cells = tb.add_row().cells
            for c, h in zip(cells, header):
                shade(c, "0B5D4B")
                run(c.paragraphs[0], str(h).upper(), True, 8, RGBColor(255, 255, 255))
        for i, r in enumerate(rows):
            cells = tb.add_row().cells
            for c, v, al in zip(cells, r, widths_align):
                if i % 2:
                    shade(c, "F8FAF9")
                par = c.paragraphs[0]
                par.alignment = WD_ALIGN_PARAGRAPH.RIGHT if al == "r" else WD_ALIGN_PARAGRAPH.LEFT
                run(par, v, size=9.5)
        if total:
            cells = tb.add_row().cells
            for c, v, al in zip(cells, total, widths_align):
                shade(c, "E6F0ED")
                c.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.RIGHT if al == "r" else WD_ALIGN_PARAGRAPH.LEFT
                run(c.paragraphs[0], v, True, 9.5)
        d.add_paragraph()

    for b in spec.get("blocks", []):
        kind = b.get("type")
        if kind == "heading":
            h = d.add_paragraph()
            h.paragraph_format.space_before = Pt(10)
            run(h, b.get("text"), True, 12, green)
        elif kind in ("paragraph", "letter"):
            run(d.add_paragraph(), b.get("text"), size=10.5 if kind == "letter" else 10)
        elif kind == "note":
            n = d.add_paragraph()
            n.paragraph_format.left_indent = Cm(0.4)
            run(n, b.get("text"), size=9, color=RGBColor(0x3E, 0x4C, 0x59))
        elif kind == "signature":
            s = d.add_paragraph("\n\n")
            run(s, "______________________________\n" + (b.get("text") or "Authorised signatory"), size=9)
        elif kind == "keyvalues":
            table([[k, v] for k, v in b.get("items", [])], ["l", "l"])
        elif kind == "stats":
            items = b.get("items", [])
            table([[s.get("value") for s in items]], ["l"] * len(items), header=[s.get("label") for s in items])
        elif kind == "bar_chart":
            items = b.get("items", [])
            top = max([float(i.get("value") or 0) for i in items] + [1e-9])
            table([[i.get("label"), "█" * max(1, round(float(i.get("value") or 0) / top * 24)),
                    inr(i.get("value")) if b.get("money", True) else i.get("value")] for i in items], ["l", "l", "r"], caption=b.get("title"))
        elif kind == "table":
            cols = b.get("columns", [])
            rows = [[_cell(v, c.get("kind", "text")) for c, v in zip(cols, r)] for r in b.get("rows", [])]
            tot = [_cell(v, c.get("kind", "text")) for c, v in zip(cols, b["total_row"])] if b.get("total_row") else None
            table(rows or [[b.get("empty") or "Nothing to show"] + [""] * (len(cols) - 1)], ["r" if _align(c) == "r" else "l" for c in cols],
                  header=[c.get("label") for c in cols], total=tot, caption=b.get("caption"))
    if spec.get("footer"):
        f = sec.footer.paragraphs[0]
        run(f, spec["footer"], size=8, color=RGBColor(0x7B, 0x87, 0x94))
    d.save(path)
    return {"pages": None}


def render(spec: dict, path: str, fmt: str = "pdf") -> dict:
    spec = {**spec, "blocks": [b for b in spec.get("blocks", []) if b.get("type") in BLOCK_TYPES]}
    return to_docx(spec, path) if fmt == "docx" else to_pdf(spec, path)
