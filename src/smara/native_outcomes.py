"""Mechanical outcome evidence, never a second planner or semantic judge."""
from __future__ import annotations

import hashlib
from pathlib import Path
import json
import re
import shlex
from urllib.parse import urldefrag

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


def command_body(command):
    """Unwrap only one recognized native shell invocation, not arbitrary scripts."""
    if not isinstance(command, str): return None
    command = command.strip()
    try:
        words = shlex.split(command, posix=True)
    except ValueError:
        return None
    if words:
        executable = words[0].replace("\\", "/").rsplit("/", 1)[-1].lower().removesuffix(".exe")
        if executable in {"powershell", "pwsh"}:
            flags = [word.lower() for word in words[1:-2]]
            if (len(words) < 3 or words[-2].lower() != "-command"
                    or any(flag not in {"-noprofile", "-noninteractive", "-nologo"} for flag in flags)):
                return None
            command = words[-1].strip()
        elif executable in {"bash", "sh"}:
            if len(words) != 3 or words[1] not in {"-c", "-lc"}: return None
            command = words[-1].strip()
    if command.startswith("& "):
        command = command[2:].lstrip()
    return command


def test_command(command):
    # Compound commands can mask an earlier failure. Do not count them.
    if not isinstance(command, str):
        return False
    # PowerShell needs its call operator for a quoted absolute executable.
    # Permit that single leading operator, never chaining or background work.
    command = command_body(command)
    if not command: return False
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


def review(spec, checkpoint, transcript, *, elapsed_seconds=0, cycle_turn_ids=None):
    kind = spec.get("task_kind", "general")
    turns = transcript.get("turns", [])
    items = [item for turn in turns for item in turn.get("items", [])]
    current_items = ([item for turn in turns if turn.get("id") in cycle_turn_ids for item in turn.get("items", [])]
                     if cycle_turn_ids is not None else turns[-1].get("items", []) if turns else [])
    last_edit = max((i for i, item in enumerate(items) if item.get("type") == "fileChange" and item.get("status") == "completed"), default=-1)
    tests, sources, failed_pages, clock = {}, {}, [], False
    for index, item in enumerate(items):
        if item.get("type") == "commandExecution" and test_command(item.get("command")) and index > last_edit:
            # Baseline runs before the last edit are not current evidence.
            # Keep the latest post-edit outcome for each exact command.
            tests[command_body(item["command"])] = {"command": command_body(item["command"])[:1000], "native_command": item["command"][:2000], "exit_code": item.get("exitCode"),
                "passed": item.get("status") == "completed" and type(item.get("exitCode")) is int and item["exitCode"] == 0,
                "failure_excerpt": str(item.get("aggregatedOutput") or "")[-4000:] if item.get("exitCode") != 0 else ""}
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
        source = next((s for s in sources.values() if urldefrag(claim["url"])[0] in {urldefrag(s["url"])[0], urldefrag(s.get("alias") or "")[0]}), None)
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
        web_claims = [c for c in claims if c["url"]]
        check("Citation excerpts", bool(web_claims) and all(c["quote_found"] for c in web_claims), "Every submitted research claim needs an exact excerpt from a page actually read. Local coding claims do not count. This checks quotation provenance, not semantic correctness or full answer coverage.")
        if spec.get("time_sensitive"):
            check("Current date", clock, "Time-sensitive work requires a native clock read in this cycle; an older session timestamp does not count.")
    outputs = [output_record(spec["workspace"], name) for name in checkpoint.get("outputs", [])]
    coverage = []
    links = {entry["requirement_id"]: entry for entry in checkpoint.get("coverage", [])}
    for requirement in spec.get("requirements", []):
        entry = links.get(requirement["id"], {})
        evidence = []
        for index in entry.get("claim_indexes", []):
            if type(index) is int and 0 <= index < len(claims) and claims[index]["quote_found"]:
                evidence.append({"kind": "citation", "reference": index})
        for name in entry.get("outputs", []):
            if any(output["path"] == name for output in outputs):
                evidence.append({"kind": "output", "reference": name})
        for cmd in entry.get("test_commands", []):
            matched = next((value for key, value in tests.items() if key == command_body(cmd)), None)
            if matched and matched["passed"]:
                evidence.append({"kind": "test", "reference": matched["command"]})
        coverage.append({**requirement, "evidence_present": bool(evidence), "evidence": evidence,
                         "semantic_review_required": True})
    if coverage:
        check("Requirement evidence", all(c["evidence_present"] for c in coverage),
              "Each explicit requirement needs observed citation, output or test evidence. Linkage does not prove that the evidence satisfies the requirement; review correctness.")
    if outputs:
        check("Output files", True, f"Hashed {len(outputs)} existing project files. Changed files will need another review.")
    for output in outputs:
        if not output["path"].lower().endswith(".pdf"):
            continue
        inspected, total, issues = set(), None, []
        for item in items:
            if item.get("type") != "mcpToolCall" or item.get("server") != "smara_readers" or item.get("tool") != "pdf_inspect":
                continue
            for value in payloads(item):
                if value.get("content_sha256") != output["sha256"]:
                    continue  # A prior revision is not current layout evidence.
                page_count = value.get("total_pages")
                if type(page_count) is not int or not 1 <= page_count <= 10000:
                    continue
                total = page_count
                for page in value.get("pages", []):
                    number = page.get("page")
                    if type(number) is int and 1 <= number <= total and page.get("rendered") is True:
                        inspected.add(number)
                        issues.extend(page.get("issues") or [])
        passed = total is not None and len(inspected) == total and not issues
        check("PDF layout preflight", passed,
              f"{output['path']}: rendered {len(inspected)}/{total or '?'} pages of the current PDF; {len(issues)} layout warnings. "
              "Resolve warnings and inspect every page with pdf_inspect. This is heuristic preflight, not visual or semantic proof; human review is still required.")
    return {"kind": kind, "passed": all(c["passed"] for c in checks), "checks": checks,
        "coverage": coverage,
        "metrics": {"citation_support": sum(c["quote_found"] for c in claims if c["url"]) / sum(bool(c["url"]) for c in claims) if any(c["url"] for c in claims) else None,
                    "evidence_coverage": sum(c["evidence_present"] for c in coverage) / len(coverage) if coverage else None,
                    "source_reads": len(sources), "failed_pages": len(failed_pages),
                    "failed_tests": sum(not t["passed"] for t in tests.values()), "latency_seconds": round(elapsed_seconds, 1),
                    "semantic_correctness": "not_automatically_verified"},
        "tests": list(tests.values())[-50:], "sources": [{k: v for k, v in s.items() if k != "texts"} for s in sources.values()][:100],
        "claims": claims, "failed_pages": failed_pages[-20:], "outputs": outputs, "elapsed_seconds": round(elapsed_seconds, 1),
        "review_required": True}


def validate_acceptance(goal):
    report = goal["checkpoint"].get("outcome")
    pdf_output = any(output["path"].lower().endswith(".pdf") for output in (report or {}).get("outputs", []))
    if pdf_output and not any(c.get("name") == "PDF layout preflight" and c.get("passed") is True for c in (report or {}).get("checks", [])):
        raise ValueError("PDF layout evidence is incomplete; continue for a fresh review")
    if (goal["spec"].get("task_kind", "general") != "general" or pdf_output) and (not report or not report.get("passed")):
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
