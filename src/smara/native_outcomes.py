"""Mechanical outcome evidence, never a second planner or semantic judge."""
from __future__ import annotations

import hashlib
from pathlib import Path
import json
import re
import shlex

KINDS = {"general", "coding", "research", "mixed"}


def output_record(workspace, name):
    root = Path(workspace).resolve(strict=True)
    path = (root / name).resolve(strict=True)
    parts = path.relative_to(root).parts
    if not path.is_file() or any(p.lower() in {".git", ".smara", ".ssh", ".aws", ".codex", "credentials.json", "id_rsa", "id_ed25519", "id_ecdsa"} or p.lower().startswith(".env") for p in parts):
        raise ValueError("Output must be a non-sensitive project file")
    if path.suffix.lower() in {".key", ".pem", ".pfx", ".p12"} or path.stat().st_size > 25 * 1024 * 1024:
        raise ValueError("Output is sensitive or exceeds the 25 MB verification limit")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return {"path": path.relative_to(root).as_posix(), "sha256": digest.hexdigest(), "bytes": path.stat().st_size}


def test_command(command):
    # Compound commands can mask an earlier failure. Do not count them.
    if not isinstance(command, str):
        return False
    # PowerShell needs its call operator for a quoted absolute executable.
    # Permit that single leading operator, never chaining or background work.
    command = command.strip()
    if command.startswith("& "):
        command = command[2:].lstrip()
    if re.search(r"[;&|`\n\r<>]", command):
        return False
    try:
        words = shlex.split(command, posix=False)
    except ValueError:
        return False
    words = [word.strip('"\'').lower() for word in words]
    if not words:
        return False
    if any(word in {"--collect-only", "--co", "--help", "-h", "--version", "--no-run", "--list", "--dry-run"} for word in words):
        return False
    executable = words[0].replace("\\", "/").rsplit("/", 1)[-1].removesuffix(".exe")
    if executable in {"python", "python3", "py"} and words[1:3] == ["-m", "pytest"]:
        return True
    if executable == "pytest":
        return True
    return executable in {"cargo", "go", "npm", "pnpm", "yarn"} and words[1:2] == ["test"]


def payloads(item):
    result = item.get("result") or {}
    if item.get("status") != "completed" or item.get("error") or result.get("isError"):
        return []
    values = []
    for block in result.get("content", []):
        if block.get("type") == "text":
            try:
                value = json.loads(block.get("text", ""))
                if isinstance(value, dict):
                    values.append(value)
            except (ValueError, TypeError):
                continue
    return values


def normalized(text):
    return " ".join(text.split())


def review(spec, checkpoint, transcript, *, elapsed_seconds=0):
    kind = spec.get("task_kind", "general")
    turns = transcript.get("turns", [])
    items = [item for turn in turns for item in turn.get("items", [])]
    current_items = turns[-1].get("items", []) if turns else []
    last_edit = max((i for i, item in enumerate(items) if item.get("type") == "fileChange" and item.get("status") == "completed"), default=-1)
    tests, sources, failed_pages, clock = {}, {}, [], False
    for index, item in enumerate(items):
        if item.get("type") == "commandExecution" and test_command(item.get("command")) and index > last_edit:
            # Baseline runs before the last edit are not current evidence.
            # Keep the latest post-edit outcome for each exact command.
            tests[item["command"]] = {"command": item["command"][:1000], "exit_code": item.get("exitCode"),
                "passed": item.get("status") == "completed" and type(item.get("exitCode")) is int and item["exitCode"] == 0}
        if item.get("type") != "mcpToolCall" or item.get("server") != "smara_readers":
            continue
        values = payloads(item)
        if item.get("tool") == "current_time" and values and item in current_items:
            clock = True
        if item.get("tool") not in {"fetch_url", "source_page"}:
            continue
        if not values:
            failed_pages.append(str((item.get("arguments") or {}).get("url", "Source read failed"))[:1000])
        for value in values:
            url = value.get("final_url") or value.get("url")
            text = value.get("text")
            if isinstance(url, str) and url.startswith(("https://", "http://")) and isinstance(text, str) and text.strip():
                source = sources.setdefault(url, {"url": url, "texts": [], "retrieved_at": value.get("retrieved_at")})
                source["texts"].append(normalized(text))
                if value.get("url") and value["url"] != url:
                    source["alias"] = value["url"]
    claims = []
    for claim in checkpoint.get("claims", []):
        source = next((s for s in sources.values() if claim["url"] in {s["url"], s.get("alias")}), None)
        quote = normalized(claim["quote"])
        found = bool(source and len(quote) >= 20 and any(quote in text for text in source["texts"]))
        claims.append({**claim, "quote_found": found})
    checks = []
    def check(name, passed, detail):
        checks.append({"name": name, "passed": passed, "detail": detail})
    if kind in {"coding", "mixed"}:
        check("Tests", bool(tests) and all(t["passed"] for t in tests.values()), "At least one native test command must pass after the last recorded edit; failed or stale commands do not count.")
    if kind in {"research", "mixed"}:
        check("Source reads", len(sources) >= spec.get("min_sources", 2), f"Read {len(sources)} distinct pages; search snippets alone do not count.")
        check("Citation excerpts", bool(claims) and all(c["quote_found"] for c in claims), "Every submitted claim needs an exact excerpt from a page actually read. This checks quotation provenance, not semantic correctness or full answer coverage.")
        if spec.get("time_sensitive"):
            check("Current date", clock, "Time-sensitive work requires a native clock read in this cycle; an older session timestamp does not count.")
    outputs = [output_record(spec["workspace"], name) for name in checkpoint.get("outputs", [])]
    if outputs:
        check("Output files", True, f"Hashed {len(outputs)} existing project files. Changed files will need another review.")
    return {"kind": kind, "passed": all(c["passed"] for c in checks), "checks": checks,
        "tests": list(tests.values())[-50:], "sources": [{k: v for k, v in s.items() if k != "texts"} for s in sources.values()][:100],
        "claims": claims, "failed_pages": failed_pages[-20:], "outputs": outputs, "elapsed_seconds": round(elapsed_seconds, 1),
        "review_required": True}


def validate_acceptance(goal):
    report = goal["checkpoint"].get("outcome")
    if goal["spec"].get("task_kind", "general") != "general" and (not report or not report.get("passed")):
        raise ValueError("Outcome evidence checks are incomplete; add feedback and continue")
    for saved in (report or {}).get("outputs", []):
        try:
            current = output_record(goal["spec"]["workspace"], saved["path"])
        except (OSError, ValueError):
            raise ValueError("Output changed or disappeared; add feedback for a fresh review") from None
        if current != saved:
            raise ValueError("Output changed since verification; add feedback for a fresh review")


def progress_text(goal):
    cp = goal["checkpoint"]
    report = cp.get("outcome") or {}
    lines = [cp.get("summary") or goal.get("detail") or "Waiting for a cycle"]
    if cp.get("completed_steps"):
        lines.append("Done: " + "; ".join(cp["completed_steps"][-5:]))
    lines.extend(f"{'PASS' if c['passed'] else 'MISSING'} · {c['name']}: {c['detail']}" for c in report.get("checks", []))
    if report.get("elapsed_seconds") is not None:
        lines.append(f"Last cycle: {report['elapsed_seconds']} seconds · Failed page reads: {len(report.get('failed_pages', []))}")
    lines.append("Next: " + (cp.get("next_step") or "Waiting for a cycle"))
    return "\n".join(lines)
