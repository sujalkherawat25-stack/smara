"""Bounded, deterministic ingestion of local PDF collections.

The collection scanner is intentionally extraction-first.  It never follows
symlinks, walks outside the supplied root, or silently drops a file/page when
a bound is reached.  Scanned pages are returned as explicit ``needs_ocr``
records so a caller can choose an OCR provider and retain that provenance.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any, Mapping


class PdfCollectionError(ValueError):
    """A user-actionable collection validation or extraction error."""


def _safe_root(root: str | Path) -> Path:
    candidate = Path(root).expanduser().resolve()
    if not candidate.exists() or not candidate.is_dir():
        raise PdfCollectionError("PDF collection root must be an existing directory")
    if candidate.is_symlink():
        raise PdfCollectionError("PDF collection root may not be a symlink")
    return candidate


def _extract_page(page: Any) -> str:
    try:
        value = page.extract_text(extraction_mode="layout") or ""
    except Exception:
        try:
            value = page.extract_text() or ""
        except Exception:
            value = ""
    return "\n".join(line.rstrip() for line in str(value).splitlines()).strip()


def _prior_pages(prior: Mapping[str, Any] | None) -> dict[tuple[str, int], Mapping[str, Any]]:
    result: dict[tuple[str, int], Mapping[str, Any]] = {}
    for item in (prior or {}).get("files", ()):
        if not isinstance(item, Mapping):
            continue
        relative = str(item.get("relative_path") or "")
        digest = str(item.get("sha256") or "")
        for page in item.get("pages", ()):
            if not isinstance(page, Mapping):
                continue
            number = int(page.get("page") or 0)
            if relative and digest and number > 0:
                result[(relative + ":" + digest, number)] = page
    return result


def scan_pdf_collection(
    root: str | Path,
    *,
    max_files: int = 50,
    max_pages: int = 2000,
    max_bytes: int = 500 * 1024 * 1024,
    max_chars_per_page: int = 60_000,
    resume_manifest: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Scan PDFs under *root* and return a resumable manifest.

    Limits are hard bounds.  A collection that would exceed one is marked
    ``limit_exceeded`` with the offending item; records already scanned remain
    available to the caller and no item is silently omitted.
    """
    try:
        import pypdf
    except ImportError as exc:  # pragma: no cover - dependency is packaged
        raise PdfCollectionError("pypdf is not installed") from exc
    root_path = _safe_root(root)
    max_files = max(1, min(int(max_files), 200))
    max_pages = max(1, min(int(max_pages), 20_000))
    max_bytes = max(1, min(int(max_bytes), 2 * 1024 * 1024 * 1024))
    max_chars_per_page = max(1, min(int(max_chars_per_page), 250_000))
    prior = _prior_pages(resume_manifest)
    files = sorted(
        (item for item in root_path.rglob("*") if item.is_file() and not item.is_symlink() and item.suffix.lower() == ".pdf"),
        key=lambda item: item.relative_to(root_path).as_posix().casefold(),
    )
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "root": str(root_path),
        "limits": {"max_files": max_files, "max_pages": max_pages, "max_bytes": max_bytes, "max_chars_per_page": max_chars_per_page},
        "status": "ok",
        "files": [],
        "errors": [],
        "needs_ocr_pages": [],
        "total_bytes": 0,
        "total_pages": 0,
        "resumed_pages": 0,
    }
    if len(files) > max_files:
        manifest["status"] = "limit_exceeded"
        manifest["errors"].append({"kind": "max_files", "limit": max_files, "observed": len(files)})
        files = files[:max_files]
    for path in files:
        relative = path.relative_to(root_path).as_posix()
        try:
            size = path.stat().st_size
            if manifest["total_bytes"] + size > max_bytes:
                manifest["status"] = "limit_exceeded"
                manifest["errors"].append({"kind": "max_bytes", "path": relative, "limit": max_bytes})
                break
            raw = path.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            reader = pypdf.PdfReader(str(path))
            page_count = len(reader.pages)
            remaining = max_pages - int(manifest["total_pages"])
            if page_count > remaining:
                manifest["status"] = "limit_exceeded"
                manifest["errors"].append({"kind": "max_pages", "path": relative, "limit": max_pages, "observed": int(manifest["total_pages"]) + page_count})
                page_count = max(0, remaining)
            file_record: dict[str, Any] = {"relative_path": relative, "sha256": digest, "bytes": size, "pages": [], "page_count": len(reader.pages)}
            for index in range(page_count):
                page_number = index + 1
                reused = prior.get((relative + ":" + digest, page_number))
                if reused is not None:
                    page_record = dict(reused)
                    page_record["resumed"] = True
                    manifest["resumed_pages"] += 1
                else:
                    text = _extract_page(reader.pages[index])
                    full_chars = len(text)
                    truncated = full_chars > max_chars_per_page
                    if truncated:
                        text = text[:max_chars_per_page]
                    page_record = {
                        "page": page_number,
                        "text": text,
                        "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                        "full_text_chars": full_chars,
                        "truncated": truncated,
                        "needs_ocr": not bool(text),
                        "resumed": False,
                    }
                file_record["pages"].append(page_record)
                if page_record.get("needs_ocr"):
                    manifest["needs_ocr_pages"].append({"relative_path": relative, "page": page_number})
                manifest["total_pages"] += 1
            manifest["files"].append(file_record)
            manifest["total_bytes"] += size
        except Exception as exc:
            manifest["status"] = "partial" if manifest["files"] else "error"
            manifest["errors"].append({"kind": "pdf_error", "path": relative, "error": type(exc).__name__})
    manifest["file_count"] = len(manifest["files"])
    manifest["manifest_sha256"] = hashlib.sha256(
        __import__("json").dumps({key: value for key, value in manifest.items() if key != "manifest_sha256"}, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return manifest


__all__ = ["PdfCollectionError", "scan_pdf_collection"]
