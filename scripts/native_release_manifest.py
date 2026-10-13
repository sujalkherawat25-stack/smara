"""Pin release inputs and artifact hashes. Never publishes or installs anything.

Allows unrelated user report deletions; rejects dirty/untracked build inputs.
This proves provenance, not correctness. Quality/installed-GUI gates are separate.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args])


def build_input(path):
    return (path.startswith(("src/", "vendor/codex/", "apps/desktop/src/", "apps/desktop/src-tauri/src/", "apps/desktop/src-tauri/capabilities/", "release/evidence/"))
        or path in {"pyproject.toml", "release/VERSION", "native/UPSTREAM.json", "vendor/codex/LICENSE", "vendor/codex/NOTICE"}
        or path.startswith("scripts/") and (Path(path).name.startswith("build-smara-") or Path(path).name in {"native_cli_entry.py", "native_desktop_entry.py", "install-smara-native.ps1"})
        or path.startswith("apps/desktop/") and Path(path).name in {"package.json", "package-lock.json", "tsconfig.json", "tsconfig.node.json", "vite.config.ts", "index.html", "Cargo.toml", "Cargo.lock", "build.rs", "tauri.conf.json"})


def source_manifest(root):
    root = root.resolve()
    changed = git(root, "diff", "--name-only", "HEAD", "-z").decode().split("\0")
    untracked = git(root, "ls-files", "--others", "--exclude-standard", "-z").decode().split("\0")
    unsafe = sorted({path for path in [*changed, *untracked] if path and build_input(path)})
    if unsafe:
        raise ValueError("Commit build inputs before packaging: " + ", ".join(unsafe))
    paths = sorted(path for path in git(root, "ls-files", "-z").decode().split("\0") if path and build_input(path))
    hashes = {path: hashlib.sha256((root / path).read_bytes()).hexdigest() for path in paths}
    digest = hashlib.sha256(json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"commit": git(root, "rev-parse", "HEAD").decode().strip(), "version": (root / "release/VERSION").read_text().strip(),
            "build_input_sha256": digest, "inputs": hashes, "unrelated_changes": [path for path in changed if path and not build_input(path)]}


def verify_source(root, saved):
    current = source_manifest(root)
    if any(current[key] != saved.get(key) for key in ("commit", "version", "build_input_sha256", "inputs")):
        raise ValueError("Source changed since the release manifest was captured; rebuild and retest")
    return current


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["capture", "verify", "seal"])
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--artifact", type=Path, action="append", default=[])
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    if args.action == "capture":
        record = source_manifest(root)
        target = args.manifest
    else:
        record = json.loads(args.manifest.read_text(encoding="utf-8"))
        verify_source(root, record)
        if args.action == "verify":
            print(json.dumps({"source_matches": True, "commit": record["commit"], "version": record["version"]}))
            return 0
        if args.out is None or not args.artifact:
            raise ValueError("Sealing requires a new output and the exact tested artifacts")
        record["artifacts"] = [{"name": path.name, "bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for path in args.artifact]
        record["quality_or_gui_approval_implied"] = False
        target = args.out
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8") as stream:
        json.dump(record, stream, indent=2)
    print(json.dumps({"manifest": str(target), "commit": record["commit"], "version": record["version"]}))
    return 0


if __name__ == "__main__": raise SystemExit(main())
