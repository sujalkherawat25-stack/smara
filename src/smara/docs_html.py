"""PDF reports and posters printed by the PC's own Edge or Chrome.

A browser shapes Hindi, Marathi, Gujarati, Tamil and the other Indian scripts correctly (vowel signs, joined letters); a
plain PDF library does not. So the page is written as HTML with the owner's text escaped, loaded with the network shut,
fitted to the page, and printed to PDF. Nothing is fetched and no page outside this HTML is opened.
"""
from __future__ import annotations

import base64
import html
import mimetypes
from pathlib import Path

from . import docs_build as build


class BrowserMissing(RuntimeError):
    pass


FONT_STACK = '"Nirmala UI","Segoe UI","Noto Sans Devanagari","Noto Sans",Arial,sans-serif'


def has_indic(*texts: str) -> bool:
    return any(ord(char) > 0x2FF and not (0x2000 <= ord(char) <= 0x206F) and ord(char) != 0x20B9 for text in texts for char in str(text or ""))


def _e(value) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _data_uri(path: Path) -> str:
    kind = mimetypes.guess_type(path.name)[0] or "image/png"
    return f"data:{kind};base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def print_pdf(page_html: str, out: Path, *, width_mm: float, height_mm: float, margin_mm: tuple = (0, 0, 0, 0),
              header: str = "", footer: str = "", timeout_ms: int = 40000) -> None:
    """Print the HTML to a PDF. Only data: pictures load; every other request is refused."""
    from playwright.sync_api import sync_playwright
    from .smara_bridge import find_browser
    executable = find_browser("auto")
    if not executable:
        raise BrowserMissing("No Edge or Chrome was found on this PC")
    with sync_playwright() as runtime:
        try:
            browser = runtime.chromium.launch(executable_path=executable, headless=True, args=["--disable-dev-shm-usage", "--disable-gpu"])
        except Exception as exc:
            raise BrowserMissing("The PC's browser could not start") from exc
        try:
            context = browser.new_context(java_script_enabled=True, service_workers="block", accept_downloads=False)
            context.route("**/*", lambda route: route.continue_() if route.request.url.startswith(("data:", "about:")) else route.abort())
            page = context.new_page()
            page.set_default_timeout(timeout_ms)
            page.set_content(page_html, wait_until="load")
            page.evaluate("document.fonts.ready.then(() => { if (window.fitAll) window.fitAll(); return true; })")
            page.emulate_media(media="print")
            top, right, bottom, left = margin_mm
            page.pdf(path=str(out), width=f"{width_mm}mm", height=f"{height_mm}mm", print_background=True, prefer_css_page_size=True,
                     margin={"top": f"{top}mm", "right": f"{right}mm", "bottom": f"{bottom}mm", "left": f"{left}mm"},
                     display_header_footer=bool(header or footer), header_template=header or "<span></span>", footer_template=footer or "<span></span>")
        finally:
            browser.close()


# ------------------------------------------------------------------------------------------------ report
def report_pdf(path: Path, spec: dict, resolve_image) -> dict:
    blocks = build.clean_blocks(spec.get("blocks"))
    primary, dark, _accent, _soft = build.theme_colors(spec.get("theme"))
    ink = build.text_on(primary)
    width_mm, height_mm = (215.9, 279.4) if str(spec.get("page") or "A4").upper() == "LETTER" else (210, 297)
    if spec.get("landscape"):
        width_mm, height_mm = height_mm, width_mm
    parts = []
    title = build.clean_text(spec.get("title"), 200)
    if title:
        parts.append(f'<h1 class="title">{_e(title)}</h1>')
    if spec.get("subtitle"):
        parts.append(f'<p class="sub">{_e(build.clean_text(spec["subtitle"], 300))}</p>')
    for block in blocks:
        kind = block["type"]
        if kind == "heading":
            parts.append(f'<h{block["level"] + 1}>{_e(block["text"])}</h{block["level"] + 1}>')
        elif kind == "paragraph":
            text = _e(block["text"]).replace("\n", "<br>")
            parts.append(f'<p style="text-align:{block["align"]}">' + (f"<b>{text}</b>" if block["bold"] else text) + "</p>")
        elif kind in {"bullets", "numbered"}:
            tag = "ul" if kind == "bullets" else "ol"
            parts.append(f"<{tag}>" + "".join(f"<li>{_e(item)}</li>" for item in block["items"]) + f"</{tag}>")
        elif kind == "table":
            header, rows, fmts = block["header"], block["rows"], block["formats"]
            columns = max([len(header)] + [len(r) for r in rows] or [1])
            out = ["<table>"]
            if header:
                out.append("<thead><tr>" + "".join(f"<th>{_e(header[i] if i < len(header) else '')}</th>" for i in range(columns)) + "</tr></thead>")
            out.append("<tbody>")
            for number, row in enumerate(rows):
                last = block["total_row"] and number == len(rows) - 1
                cells = []
                for i in range(columns):
                    value = row[i] if i < len(row) else ""
                    text = build.display(value, (fmts[i] if i < len(fmts) else None) if build.is_number(value) else None)
                    cells.append(f'<td class="{"num" if build.is_number(value) else ""}">{_e(text)}</td>')
                out.append(f'<tr class="{"total" if last else ""}">' + "".join(cells) + "</tr>")
            out.append("</tbody></table>")
            parts.append("".join(out))
        elif kind == "image":
            parts.append(f'<p><img src="{_data_uri(resolve_image(block["path"]))}" style="width:{block["width_cm"]}cm"></p>')
        elif kind == "page_break":
            parts.append('<div style="break-after:page"></div>')
        elif kind == "spacer":
            parts.append('<div style="height:14pt"></div>')
    document = f"""<!doctype html><html><head><meta charset="utf-8"><style>
body{{font-family:{FONT_STACK};font-size:10.5pt;line-height:1.5;color:#16181B;margin:0}}
h1.title{{color:{dark};font-size:22pt;margin:0 0 3pt;line-height:1.2}} .sub{{color:#555B63;font-size:11.5pt;margin:0 0 12pt}}
h2{{color:{primary};font-size:16pt;margin:16pt 0 6pt}} h3{{color:{primary};font-size:13pt;margin:12pt 0 5pt}} h4{{color:{primary};font-size:11.5pt;margin:10pt 0 4pt}}
p{{margin:0 0 7pt}} ul,ol{{margin:0 0 8pt;padding-left:20pt}} li{{margin-bottom:3pt}}
table{{border-collapse:collapse;width:100%;margin:8pt 0 12pt;font-size:10pt}} thead{{display:table-header-group}} tr{{break-inside:avoid}}
th{{background:{primary};color:{ink};text-align:left;padding:5pt 7pt;border:1px solid #C9CED3}}
td{{padding:5pt 7pt;border:1px solid #C9CED3;vertical-align:top}} td.num{{text-align:right;font-variant-numeric:tabular-nums}}
tr.total td{{font-weight:700;background:#E7ECEF}} img{{max-width:100%}}
</style></head><body>{''.join(parts)}</body></html>"""
    style = "font-size:8pt;color:#555B63;width:100%;text-align:center;font-family:Arial,sans-serif;"
    footer_text = _e(build.clean_text(spec.get("footer_text"), 200))
    footer = f'<div style="{style}">{footer_text + " &nbsp; " if footer_text else ""}Page <span class="pageNumber"></span> of <span class="totalPages"></span></div>'
    header = f'<div style="{style}text-align:left;padding-left:18mm;">{_e(build.clean_text(spec["header_text"], 120))}</div>' if spec.get("header_text") else ""
    print_pdf(document, path, width_mm=width_mm, height_mm=height_mm, margin_mm=(18, 18, 18, 18), header=header, footer=footer)
    return {"blocks": len(blocks), "engine": "browser"}


# ------------------------------------------------------------------------------------------------ poster
def poster_pdf(path: Path, spec: dict) -> dict:
    title = build.clean_text(spec.get("title"), 120)
    if not title:
        raise ValueError("A poster needs a title")
    tagline, highlight = build.clean_text(spec.get("tagline"), 160), build.clean_text(spec.get("highlight"), 40)
    details = [build.clean_text(d, 140) for d in (spec.get("details") or [])][:8]
    cta, footer = build.clean_text(spec.get("call_to_action"), 120), build.clean_text(spec.get("footer"), 160)
    primary, dark, accent, soft = build.theme_colors(spec.get("theme"))
    accent = build.hex_color(spec.get("accent_color"), accent)
    size_name = str(spec.get("size") or "A4")
    size_name = size_name if size_name in build.POSTER_SIZES else "A4"
    w_mm, h_mm = build.POSTER_SIZES[size_name]
    if spec.get("landscape") and size_name in {"A4", "A3", "A5"}:
        w_mm, h_mm = h_mm, w_mm
    unit = min(w_mm, h_mm) / 210          # millimetres per "A4 millimetre"
    band = 0.34 if details or highlight else 0.52
    rows = "".join(f'<div class="row"><i></i><div class="box"><span class="fit" data-max="{int(unit * 56)}" data-min="9">{_e(line)}</span></div></div>' for line in details)
    badge = (f'<div class="badge"><span class="fit" data-max="{int(unit * 100)}" data-min="12">{_e(highlight)}</span></div>' if highlight else "")
    page_html = f"""<!doctype html><html><head><meta charset="utf-8"><style>
@page{{size:{w_mm}mm {h_mm}mm;margin:0}}
*{{box-sizing:border-box}} html,body{{margin:0;padding:0}}
body{{width:{w_mm}mm;height:{h_mm}mm;overflow:hidden;background:{soft};font-family:{FONT_STACK};display:flex;flex-direction:column;color:#16181B}}
.band{{flex:0 0 {band * 100:.1f}%;background:{primary};color:{build.text_on(primary)};text-align:center;display:flex;flex-direction:column;justify-content:center;align-items:center;
  padding:{8 * unit}mm {12 * unit}mm;border-bottom:{4 * unit}mm solid {accent};gap:{4 * unit}mm}}
.band .t{{flex:1 1 auto;width:100%;min-height:0;display:flex;align-items:center;justify-content:center}} .band .g{{flex:0 0 {"22%" if tagline else "0"};width:100%;display:flex;align-items:center;justify-content:center}}
.fit{{display:block;width:100%;line-height:1.16}} .title{{font-weight:800;text-transform:uppercase}} .tag{{font-weight:400}}
.badge{{margin:{11 * unit}mm auto {5 * unit}mm;width:80%;flex:0 0 {11 * unit * 2.4}mm;background:{accent};color:{build.text_on(accent)};border-radius:{9 * unit}mm;text-align:center;font-weight:800;text-transform:uppercase;display:flex;align-items:center;justify-content:center;padding:0 {6 * unit}mm}}
.details{{flex:1 1 auto;display:flex;flex-direction:column;justify-content:space-evenly;padding:{4 * unit}mm {14 * unit}mm;min-height:0}}
.row{{flex:1 1 0;display:flex;align-items:center;gap:{6 * unit}mm;min-height:0}} .row i{{flex:0 0 {7 * unit}mm;height:{7 * unit}mm;border-radius:50%;background:{accent}}}
.row .box{{flex:1;height:100%;display:flex;align-items:center;min-width:0;overflow:hidden}}
.cta{{flex:0 0 {13 if cta else 0}%;background:{dark};color:{build.text_on(dark)};display:flex;align-items:center;justify-content:center;text-align:center;padding:0 {12 * unit}mm;font-weight:800}}
.foot{{flex:0 0 {12 * unit if footer else 0}mm;background:{dark};color:{build.text_on(dark)};text-align:center;display:flex;align-items:center;justify-content:center;border-top:1px solid rgba(255,255,255,.15)}}
.box2{{width:100%;height:100%;display:flex;align-items:center;justify-content:center;overflow:hidden}}
</style></head><body>
<div class="band"><div class="t"><span class="fit title" data-max="{int(unit * 170)}" data-min="20">{_e(title)}</span></div>{('<div class="g"><span class="fit tag" data-max=%d data-min="10">%s</span></div>' % (int(unit * 52), _e(tagline))) if tagline else ""}</div>
{badge}<div class="details">{rows}</div>
{('<div class="cta"><div class="box2"><span class="fit" data-max=%d data-min="12">%s</span></div></div>' % (int(unit * 90), _e(cta))) if cta else ""}
{('<div class="foot"><span class="fit" data-max=%d data-min="7">%s</span></div>' % (int(unit * 22), _e(footer))) if footer else ""}
<script>
function fitOne(el){{var max=+el.dataset.max,min=+el.dataset.min,s=max,box=el.parentElement;el.style.fontSize=s+'px';
 while((el.scrollHeight>box.clientHeight+1||el.scrollWidth>box.clientWidth+1)&&s>min){{s-=1;el.style.fontSize=s+'px';}}}}
window.fitAll=function(){{document.querySelectorAll('.fit').forEach(fitOne);var r=[].slice.call(document.querySelectorAll('.row .fit'));if(r.length){{var m=Math.min.apply(null,r.map(function(e){{return parseFloat(e.style.fontSize)}}));r.forEach(function(e){{e.style.fontSize=m+'px'}});}}}};
window.fitAll();
</script></body></html>"""
    print_pdf(page_html, path, width_mm=w_mm, height_mm=h_mm)
    return {"size": size_name, "details": len(details), "engine": "browser"}
