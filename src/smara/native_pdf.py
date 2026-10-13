"""Local PDF rendering and bounded layout preflight, not a visual-quality judge.

MCP uses the read-only entry point. PNG export is CLI-only and therefore runs
under the native command sandbox. No browser, upload, credentials or JS engine.
Contrast/bounds checks are heuristics: diagrams, overlays and rotated pages
still require visual review. PDFium calls are serialized (not thread-safe).
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
from pathlib import Path
from statistics import median
import sys
import threading

from .native_documents import DocumentError, read_document

_PDF_LOCK = threading.Lock()
MAX_SAMPLE_CHARS = 1200
MAX_PAGE_POINTS = 14400


def _luminance(rgb):
    values = [v / 255 for v in rgb]
    linear = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in values]
    return sum(a * b for a, b in zip(linear, (.2126, .7152, .0722)))


def _contrast(foreground, background):
    a, b = sorted((_luminance(foreground), _luminance(background)))
    return (b + .05) / (a + .05)


def inspect_pdf(raw, *, start_page=1, max_pages=4, render_dir=None):
    if type(start_page) is not int or start_page < 1 or type(max_pages) is not int or not 1 <= max_pages <= 4:
        raise DocumentError("Select start_page >= 1 and max_pages between 1 and 4.")
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:
        raise DocumentError("Local PDF renderer is not installed. Install the declared pypdfium2 dependency; do not claim visual verification.") from exc
    # Byte input avoids changing-file races. No forms, JavaScript or remote links
    # are activated. Close native handles and raster buffers on every path.
    with _PDF_LOCK:
        try:
            with pdfium.PdfDocument(raw) as document:
                count = len(document)
                if not count or start_page > count:
                    raise DocumentError("Selected page is outside this PDF.")
                result = []
                end = min(count, start_page - 1 + max_pages)
                for index in range(start_page - 1, end):
                    page = document[index]
                    try:
                        width, height = page.get_size()
                        if min(width, height) < 1 or max(width, height) > MAX_PAGE_POINTS:
                            raise DocumentError("PDF page dimensions exceed the local renderer safety limit.")
                        scale = min(1.5, 1600 / max(width, height))
                        bitmap = page.render(scale=scale)
                        try:
                            image = bitmap.to_pil().convert("RGB")
                        finally:
                            bitmap.close()
                        try:
                            if render_dir is not None:
                                directory = Path(render_dir)
                                directory.mkdir(parents=True, exist_ok=True)
                                image.save(directory / f"page-{index + 1:03d}.png")
                            textpage = page.get_textpage()
                            try:
                                n = textpage.count_chars()
                                text = textpage.get_text_bounded()
                                rotated = page.get_rotation() != 0 or page.get_bbox()[:2] != (0, 0)
                                visible, low, outside, bounds = 0, 0, 0, []
                                # Sample across the whole page, not only the title.
                                step = max(1, (n + MAX_SAMPLE_CHARS - 1) // MAX_SAMPLE_CHARS)
                                for char in range(0, n, step):
                                    value = pdfium.raw.FPDFText_GetUnicode(textpage, char)
                                    if not value or chr(value).isspace():
                                        continue
                                    left, bottom, right, top = textpage.get_charbox(char)
                                    if right <= left or top <= bottom or rotated:
                                        continue
                                    visible += 1
                                    bounds.append((bottom, top))
                                    if left < -.5 or bottom < -.5 or right > width + .5 or top > height + .5:
                                        outside += 1
                                        continue
                                    rgba = [ctypes.c_uint() for _ in range(4)]
                                    if not pdfium.raw.FPDFText_GetFillColor(textpage, char, *rgba):
                                        continue
                                    # Sample a ring outside the glyph. Median reduces
                                    # anti-aliasing/nearby glyph contamination. This
                                    # is only an estimated local background color.
                                    x0, x1 = int(left * scale), int(right * scale)
                                    y0, y1 = int((height - top) * scale), int((height - bottom) * scale)
                                    points = [(x, y) for x in range(x0 - 2, x1 + 3, 2) for y in (y0 - 2, y1 + 2)]
                                    points += [(x, y) for y in range(y0, y1 + 1, 2) for x in (x0 - 2, x1 + 2)]
                                    pixels = [image.getpixel((x, y)) for x, y in points if 0 <= x < image.width and 0 <= y < image.height]
                                    if not pixels:
                                        continue
                                    bg = [median(p[k] for p in pixels) for k in range(3)]
                                    alpha = rgba[3].value / 255
                                    fg = [rgba[k].value * alpha + bg[k] * (1 - alpha) for k in range(3)]
                                    if _contrast(fg, bg) < 3:
                                        low += 1
                                issues = []
                                if visible and low / visible > .08:
                                    issues.append("low_contrast_text")
                                if outside:
                                    issues.append("text_outside_page")
                                if not text.strip():
                                    issues.append("no_extractable_text")
                                if index and len(text.strip()) < 350 and bounds and (max(t for _, t in bounds) - min(b for b, _ in bounds)) / height < .35:
                                    issues.append("sparse_page_check_pagination")
                                if rotated:
                                    issues.append("rotated_or_offset_page_requires_visual_review")
                                result.append({"page": index + 1, "width_points": round(width, 2), "height_points": round(height, 2),
                                               "text_chars": len(text.strip()), "sampled_chars": visible,
                                               "low_contrast_sample_ratio": round(low / visible, 3) if visible else None,
                                               "outside_sampled_chars": outside, "issues": issues, "rendered": True})
                            finally:
                                textpage.close()
                        finally:
                            image.close()
                    finally:
                        page.close()
        except DocumentError:
            raise
        except Exception as exc:
            # PDF strings and native/parser exception messages are untrusted.
            raise DocumentError("Local PDF rendering failed; no visual verification was completed.") from exc
    return {"content_sha256": hashlib.sha256(raw).hexdigest(), "total_pages": count, "pages": result,
            "next_page": end + 1 if end < count else None,
            "visual_review_completed": False,
            "limitations": "Rendered layout preflight only. Contrast and sparse-page checks are heuristic, not semantic or full visual verification. Review page images before delivery."}


def inspect_workspace_pdf(workspace, path, *, start_page=1, max_pages=4):
    if not isinstance(path, str) or Path(path).suffix.lower() != ".pdf":
        raise DocumentError("Select one PDF inside the workspace.")
    root = Path(workspace).resolve(strict=True)
    target = (root / path).resolve(strict=True)
    if not target.is_relative_to(root) or any(p.lower() in {".git", ".smara", ".codex", ".aws", ".ssh"} for p in target.relative_to(root).parts):
        raise DocumentError("PDF path must remain inside the workspace, outside internal state folders.")
    _, raw, _ = read_document(root, path)
    result = inspect_pdf(raw, start_page=start_page, max_pages=max_pages)
    # An explicit shell action exports images under native permissions. Never
    # write workspace files from the out-of-sandbox, read-only MCP server.
    result["preview_command_argv"] = ([sys.executable, "--native-pdf"] if getattr(sys, "frozen", False)
                                      else [sys.executable, "-m", "smara.native_pdf"])
    result["preview_command_argv"] += [str(target), "--start-page", str(start_page), "--render-dir", str(root / "pdf-previews")]
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--render-dir", type=Path, help="Explicit PNG output directory; CLI only, never exposed through MCP")
    parser.add_argument("--start-page", type=int, default=1)
    args = parser.parse_args(argv)
    try:
        _, raw, _ = read_document(args.path.resolve().parent, args.path.name)
        result = inspect_pdf(raw, start_page=args.start_page, render_dir=args.render_dir)
    except (OSError, DocumentError) as exc:
        print(json.dumps({"error": str(exc) if isinstance(exc, DocumentError) else "Selected PDF or preview directory is not accessible."}), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=True))
    return 1 if any(page["issues"] for page in result["pages"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
