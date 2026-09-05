"""Shared, conservative scoring and trace helpers for external evaluations.

The helpers deliberately avoid substring matches, answer registries, and
synthetic pass conditions.  A result is either strictly scored, unsupported,
or an execution error.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any


def normalize_answer(value: Any) -> str:
    """Normalize formatting only; preserve answer meaning and order."""
    text = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
    text = re.sub(r"\s+", " ", text)
    return text.strip(" \t\r\n.,;:")


def extract_final_answer(answer: Any) -> str:
    """Use an explicit final-answer marker, otherwise retain the full answer.

    Keeping full unmarked output prevents a scorer from finding a coincidental
    number or phrase inside a long explanation and calling it correct.
    """
    text = str(answer or "").strip()
    match = re.search(r"(?:^|\n)\s*(?:final\s+answer|answer)\s*:\s*(.+)\s*$", text, re.IGNORECASE | re.DOTALL)
    return match.group(1).strip() if match else text


def strict_answer_match(candidate: Any, expected: Any) -> bool:
    """Exact normalized answer equality without permissive substrings."""
    candidate_text = normalize_answer(extract_final_answer(candidate))
    expected_text = normalize_answer(expected)
    if not candidate_text or not expected_text:
        return False
    return candidate_text == expected_text


def digest(value: Any) -> str:
    """Produce a traceable digest without serializing raw local contents."""
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(encoded.encode("utf-8", errors="replace")).hexdigest()


def safe_trace(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return capability/result hashes only, protecting file contents and keys."""
    trace: list[dict[str, Any]] = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        trace.append(
            {
                "iteration": step.get("iteration"),
                "capability": step.get("capability"),
                "ok": step.get("ok"),
                "payload_sha256": digest(step.get("payload") or {}),
                "result_sha256": digest(step.get("result") or step.get("error") or ""),
            }
        )
    return trace


def write_report(path: Path, report: dict[str, Any]) -> Path:
    """Atomically save a reproducible JSON report."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)
    return path
