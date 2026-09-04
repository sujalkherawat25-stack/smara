"""Smara Autonomous Agent Tools Suite.

Provides real, production-ready tool implementations for autonomous problem solving:
- web_search: Live search via Tavily API
- web_extract: Full page text extraction
- wayback_extract: Historical archive extraction via Wayback Machine
- python_execute: Subprocess Python 3.11 execution with timeout and output capture
- file_read: Multi-format document reader (.pdf, .xlsx, .docx, .pdb, .txt, .csv)
- zip_extract_and_read: Batch archive extractor and reader
- calculate: Math evaluation
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def web_search(query: str, api_key: Optional[str] = None, max_results: int = 5, limit: Optional[int] = None) -> str:
    """Execute live web search using Tavily API."""
    count = limit or max_results or 5
    if not api_key:
        api_key = os.getenv("TAVILY_API_KEY", "")
        if not api_key:
            try:
                from benchmarks.gaia_official_runner import get_vault_secret
                api_key = get_vault_secret("TAVILY_API_KEY")
            except Exception:
                pass
    if not api_key or not query.strip():
        return "Error: Missing Tavily API key or query."
    url = "https://api.tavily.com/search"
    payload = json.dumps({
        "api_key": api_key,
        "query": query,
        "search_depth": "advanced",
        "max_results": count
    }).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            results = data.get("results", [])
            if not results:
                return "No search results found."
            formatted = []
            for idx, r in enumerate(results[:limit]):
                title = r.get("title", "")
                link = r.get("url", "")
                snippet = r.get("content", "")
                formatted.append(f"[{idx+1}] {title}\nURL: {link}\n{snippet}")
            return "\n\n".join(formatted)
    except Exception as e:
        return f"Search Error: {e}"


def clean_html(html_text: str) -> str:
    """Strip scripts, styles, and HTML tags to produce clean readable text."""
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html_text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<(br|p|div|li|tr)[^>]*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
    )
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines)


def web_extract(url: str, max_chars: int = 5000) -> str:
    """Fetch URL and extract readable plain text."""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 SmaraAgent/1.0"}
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            raw_bytes = resp.read()
            charset = resp.headers.get_content_charset() or "utf-8"
            html_text = raw_bytes.decode(charset, errors="replace")
            cleaned = clean_html(html_text)
            if len(cleaned) > max_chars:
                return cleaned[:max_chars] + f"\n... [Truncated {len(cleaned) - max_chars} characters]"
            return cleaned or "Web page content was empty."
    except Exception as e:
        return f"Extract Error: {e}"


def wayback_extract(url: str, timestamp: str = "", target_date: str = "", max_chars: int = 5000) -> str:
    """Find and extract a historical snapshot from archive.org Wayback Machine."""
    ts = timestamp or target_date or ""
    clean_date = re.sub(r"[^0-9]", "", ts) or "20210322"
    api_url = f"https://archive.org/wayback/available?url={urllib.parse.quote(url)}&timestamp={clean_date}"
    req = urllib.request.Request(
        api_url,
        headers={"User-Agent": "SmaraAgent/1.0"}
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            snapshots = data.get("archived_snapshots", {})
            closest = snapshots.get("closest", {})
            snapshot_url = closest.get("url")
            if not snapshot_url:
                return f"No Wayback Machine snapshot found for {url} near date {target_date}."
            return f"Found snapshot ({closest.get('timestamp', '')}):\n" + web_extract(snapshot_url, max_chars=max_chars)
    except Exception as e:
        return f"Wayback Error: {e}"


def python_execute(code: str, timeout: int = 30) -> str:
    """Execute Python code in an isolated subprocess and return output."""
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as f:
        f.write(code)
        temp_path = f.name

    try:
        result = subprocess.run(
            [sys.executable, temp_path],
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace"
        )
        out = result.stdout.strip()
        err = result.stderr.strip()
        if result.returncode != 0:
            return f"Process exited with code {result.returncode}:\n{err or out}"
        return out or "(Script executed successfully with no stdout output)"
    except subprocess.TimeoutExpired:
        return f"Execution timed out after {timeout} seconds."
    except Exception as e:
        return f"Execution error: {e}"
    finally:
        try:
            os.remove(temp_path)
        except OSError:
            pass


def file_read(file_path: Path | str, max_chars: int = 6000) -> str:
    """Extract content from various file formats (.txt, .pdf, .docx, .xlsx, .pdb, .csv)."""
    p = Path(file_path)
    if not p.exists():
        return f"Error: File not found at {file_path}"

    ext = p.suffix.lower()

    if ext in [".txt", ".csv", ".json", ".md", ".py"]:
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
            return text[:max_chars]
        except Exception as e:
            return f"Error reading text file: {e}"

    if ext == ".docx":
        try:
            with zipfile.ZipFile(p) as zf:
                xml_content = zf.read("word/document.xml")
            tree = ET.fromstring(xml_content)
            paragraphs = []
            for p_tag in tree.iter():
                if p_tag.tag.endswith("p"):
                    texts = [node.text for node in p_tag.iter() if node.text]
                    if texts:
                        paragraphs.append("".join(texts))
            return "\n".join(paragraphs)[:max_chars]
        except Exception as e:
            return f"Error reading docx: {e}"

    if ext == ".xlsx":
        try:
            import openpyxl
            wb = openpyxl.load_workbook(p, data_only=True)
            lines = []
            for sheet in wb.sheetnames[:3]:
                ws = wb[sheet]
                lines.append(f"--- Sheet: {sheet} ---")
                for row in list(ws.iter_rows(values_only=True))[:100]:
                    if any(v is not None for v in row):
                        lines.append("\t".join(str(v) if v is not None else "" for v in row))
            return "\n".join(lines)[:max_chars]
        except Exception as e:
            return f"Error reading xlsx: {e}"

    if ext == ".pdf":
        try:
            import pypdf
            reader = pypdf.PdfReader(str(p))
            pages = []
            for idx, page in enumerate(reader.pages[:10]):
                txt = page.extract_text() or ""
                pages.append(f"[Page {idx+1}]\n{txt}")
            return "\n\n".join(pages)[:max_chars]
        except Exception as e:
            return f"Error reading pdf: {e}"

    if ext == ".pdb":
        try:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
            atom_lines = [l for l in lines if l.startswith(("ATOM", "HETATM"))]
            header = [l for l in lines if l.startswith(("HEADER", "TITLE", "COMPND"))]
            summary = "\n".join(header + atom_lines[:30])
            return f"PDB Structure ({len(atom_lines)} atoms total):\n{summary}"
        except Exception as e:
            return f"Error reading pdb: {e}"

    return f"Unsupported file extension: {ext}"


def zip_extract_and_read(zip_path: Path | str, max_files: int = 25) -> str:
    """Extract a ZIP archive and summarize/read text contents of files inside."""
    p = Path(zip_path)
    if not p.exists():
        return f"Error: File not found at {zip_path}"

    with tempfile.TemporaryDirectory() as tmp_dir:
        try:
            with zipfile.ZipFile(p, "r") as zf:
                zf.extractall(tmp_dir)

            extracted_files = list(Path(tmp_dir).rglob("*"))
            data_files = [f for f in extracted_files if f.is_file()]
            output_parts = [f"ZIP Archive extracted {len(data_files)} files:"]

            for f in data_files[:max_files]:
                rel_name = f.relative_to(tmp_dir)
                content = file_read(f, max_chars=1000)
                output_parts.append(f"\n=== File: {rel_name} ===\n{content}")

            return "\n".join(output_parts)
        except Exception as e:
            return f"Error extracting zip: {e}"


def calculate(expression: str) -> str:
    """Safely evaluate mathematical expressions."""
    allowed = {
        "abs": abs, "round": round, "min": min, "max": max,
        "sqrt": math.sqrt, "sin": math.sin, "cos": math.cos,
        "tan": math.tan, "log": math.log, "log10": math.log10,
        "pi": math.pi, "e": math.e, "pow": pow
    }
    cleaned = expression.replace("^", "**").replace("×", "*").replace("÷", "/")
    if not re.match(r"^[0-9\.\s\+\-\*/\(\)\,\_a-zA-Z]+$", cleaned):
        return "Error: Invalid characters in expression."
    try:
        res = eval(cleaned, {"__builtins__": {}}, allowed)
        return str(res)
    except Exception as e:
        return f"Math Error: {e}"
