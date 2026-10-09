"""Redacted read-only scan of Git history and non-ignored workspace files.

Pattern detection is not proof of validity or exhaustive secret discovery.
Never emits matched values, excerpts, environment variables, or Git URLs.
Known fixtures are reported for review, not automatically dismissed.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess


PATTERNS = {
    "smara_provider_key": rb"\bsk_(?:mem_|pvl)[A-Za-z0-9_]{16,}\b",
    "openai_style_key": rb"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{24,}\b",
    "github_token": rb"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{60,})\b",
    "aws_access_key_id": rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b",
    "google_api_key": rb"\bAIza[A-Za-z0-9_-]{35}\b",
    "slack_token": rb"\bxox[baprs]-[A-Za-z0-9-]{20,}\b",
    "private_key_block": rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----",
    "literal_credential_assignment": rb"(?im)(?:api[_-]?key|access[_-]?token|client[_-]?secret|password)\s*[:=]\s*[\"']([A-Za-z0-9+/=_-]{24,})[\"']",
}
RULES = {name: re.compile(pattern) for name, pattern in PATTERNS.items()}
MAX_BLOB = 32 * 1024 * 1024
# One reviewed, deliberately invalid string in the existing secret-rejection
# test. No value is stored here; neither a test directory nor a rule is exempt.
REVIEWED_FIXTURES = {("tests/test_production_skills_and_plugins.py", "openai_style_key", "3be4f43fbb3dac59")}


def scan(data, path, scope, blob=None):
    results = []
    for rule, pattern in RULES.items():
        for match in pattern.finditer(data):
            secret = match.group(1) if match.lastindex else match.group()
            results.append({"rule": rule, "path": path, "scope": scope,
                            "line": data.count(b"\n", 0, match.start()) + 1,
                            "fingerprint": hashlib.sha256(secret).hexdigest()[:16],
                            "blob": blob, "validity": "not tested", "value": "REDACTED"})
    return results


def git(root, *args):
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, check=True)
    return result.stdout


def audit(root):
    findings, skipped = [], []
    files = git(root, "ls-files", "--cached", "--others", "--exclude-standard", "-z").decode("utf-8").split("\0")
    for name in sorted(set(filter(None, files))):
        path = root / name
        try:
            if not path.resolve().is_relative_to(root):
                skipped.append({"path": name, "reason": "outside workspace"})
            elif path.is_file():
                if path.stat().st_size > MAX_BLOB:
                    skipped.append({"path": name, "reason": "size limit"})
                else:
                    findings.extend(scan(path.read_bytes(), name, "working tree"))
        except OSError:
            skipped.append({"path": name, "reason": "unreadable"})
    objects = [line.split(" ", 1) for line in git(root, "rev-list", "--objects", "--all").decode("utf-8").splitlines()]
    metadata = subprocess.run(["git", "cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)"], cwd=root,
                              input="\n".join(item[0] for item in objects).encode() + b"\n", capture_output=True, check=True).stdout.decode().splitlines()
    blob_count = 0
    process = subprocess.Popen(["git", "cat-file", "--batch"], cwd=root, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        for (object_id, *names), detail in zip(objects, metadata):
            _, kind, size = detail.split()
            if kind != "blob":
                continue
            path = names[0] if names else "<unnamed object>"
            if int(size) > MAX_BLOB:
                skipped.append({"blob": object_id, "path": path, "reason": "size limit"})
                continue
            process.stdin.write((object_id + "\n").encode())
            process.stdin.flush()
            header = process.stdout.readline().decode().split()
            if len(header) != 3 or header[1] != "blob":
                raise RuntimeError("Git returned an unexpected object")
            data = process.stdout.read(int(header[2]))
            if process.stdout.read(1) != b"\n":
                raise RuntimeError("Git object boundary invalid")
            findings.extend(scan(data, path, "reachable Git history", object_id))
            blob_count += 1
    finally:
        process.stdin.close()
        process.wait(timeout=15)
    return {"timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "status": "review findings" if findings else "no pattern matches",
            "scope": "working tree plus locally reachable branches/tags; not remote release assets or deleted GitHub objects",
            "git_blobs_scanned": blob_count, "files_considered": len(set(files)) - 1,
            "findings": findings, "skipped": skipped,
            "limitations": "Patterns cannot establish credential validity or prove the absence of every secret. No values sent to a service."}


def verify_public_fixture_origin(root, result):
    manifest = json.loads((root / "native/UPSTREAM.json").read_text(encoding="utf-8"))
    revision = manifest["commit"]
    response = subprocess.run(["gh", "api", f"repos/openai/codex/git/trees/{revision}?recursive=1"],
                              capture_output=True, check=True, timeout=60)
    tree = json.loads(response.stdout)
    if tree.get("truncated"):
        raise RuntimeError("Upstream tree truncated; do not infer missing hashes")
    public = {entry["path"]: entry["sha"] for entry in tree["tree"] if entry["type"] == "blob"}
    unknown = []
    for finding in result["findings"]:
        path = finding["path"]
        if path.startswith("vendor/codex/"):
            expected = public.get(path.removeprefix("vendor/codex/"))
            actual = finding["blob"] or git(root, "hash-object", "--", path).decode().strip()
            if actual == expected:
                finding["classification"] = "byte-identical public upstream source/test data; not a newly introduced Smara credential"
                finding["upstream_commit"] = revision
                continue
        if (path, finding["rule"], finding["fingerprint"]) in REVIEWED_FIXTURES:
            finding["classification"] = "reviewed synthetic fixture testing secret rejection; exact fingerprint only"
            continue
        unknown.append({key: finding[key] for key in ("path", "line", "rule", "scope", "fingerprint")})
    result["unclassified_findings"] = unknown
    result["public_fixture_origin_checked"] = True
    result["status"] = "unclassified findings require review" if unknown else "all detected patterns have reviewed public/synthetic origins"


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=root / "build/secret-audit.json")
    parser.add_argument("--verify-upstream", action="store_true", help="Read public upstream tree metadata through gh; upload no local files")
    parser.add_argument("--gate", action="store_true", help="Fail for unknown findings or skipped inputs; requires --verify-upstream")
    args = parser.parse_args()
    if args.gate and not args.verify_upstream:
        parser.error("--gate requires --verify-upstream")
    path = args.report.resolve()
    if not path.is_relative_to(root / "build"):
        raise ValueError("Private audit reports must stay under ignored build")
    result = audit(root)
    if args.verify_upstream:
        verify_public_fixture_origin(root, result)
    path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key not in {"findings", "skipped"}}))
    print(json.dumps({"finding_count": len(result["findings"]), "skipped_count": len(result["skipped"]), "report": str(path)}))
    return 1 if args.gate and (result["unclassified_findings"] or result["skipped"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
