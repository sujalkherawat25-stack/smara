"""Local, user-directed Git review. No shell, provider, or agent execution.

Only exact registered repository roots are accepted. Preview tokens bind actions
to reviewed file/index contents. Turn undo reverses a sealed local snapshot patch;
pre-existing changes and non-conflicting subsequent changes are retained.
"""
from __future__ import annotations

import argparse
import base64
import contextlib
import difflib
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import time
import uuid

from .native_profiles import state_path

MAX_FILE = 1_000_000
MAX_TOTAL = 8_000_000
MAX_FILES = 1000
MAX_PATCH = 200_000  # A single escaped IPC reply stays below the manager's limit.


def storage():
    root = state_path().parent / "native-changes"
    root.mkdir(parents=True, exist_ok=True)
    return root


@contextlib.contextmanager
def review_lock():
    path = storage() / "review.lock"
    handle = path.open("a+b")
    # Do not read byte zero before acquiring it: Windows rejects that read
    # when another process/thread already owns the byte-range lock.
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    if os.name == "nt":
        import msvcrt
        acquire = lambda: msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        release = lambda: msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        acquire = lambda: fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        release = lambda: fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    deadline = time.monotonic() + 8
    while True:
        try:
            acquire()
            break
        except OSError:
            if time.monotonic() >= deadline:
                handle.close()
                raise ValueError("Another review action is running. Wait, then refresh; do not repeat an uncertain write.") from None
            time.sleep(.05)
    try:
        yield
    finally:
        try:
            release()
        finally:
            handle.close()  # OS releases locks after a killed process as well.


def save(value):
    identity = uuid.uuid4().hex
    (storage() / f"{identity}.json").write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return identity


def load(identity, kind, workspace):
    if not isinstance(identity, str) or not re.fullmatch(r"[0-9a-f]{32}", identity):
        raise ValueError("Invalid local review identity")
    try:
        value = json.loads((storage() / f"{identity}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ValueError("Local review is unavailable; refresh Changes") from None
    if value.get("kind") != kind or value.get("workspace") != str(workspace):
        raise ValueError("This review belongs to another project or operation")
    return value


class Repository:
    def __init__(self, workspace):
        from .native_management import folder, preferences
        self.root = Path(folder(workspace))
        if not any(Path(p["workspace"]).is_dir() and Path(folder(p["workspace"])) == self.root for p in preferences()["projects"]):
            raise ValueError("Add this exact folder as a project first")
        self.environment = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        self.environment.update(GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0")
        self.options = ["-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false"]
        top = self.git("rev-parse", "--show-toplevel", allow_failure=True)
        if top.returncode or Path(top.stdout.decode("utf-8").strip()).resolve() != self.root:
            raise ValueError("Changes requires a registered Git repository root; parent repositories are not accessed")
        # Even status/diff can invoke configured clean/fsmonitor helpers. Disable
        # executable filters, and exclude filtered files from reversible actions.
        filters = self.git("config", "--name-only", "--get-regexp", r"^filter\..*\.(clean|smudge|process|required)$", allow_failure=True)
        for key in filters.stdout.decode("utf-8").splitlines():
            self.options.extend(["-c", f"{key}={'false' if key.endswith('.required') else ''}"])

    def git(self, *args, data=None, allow_failure=False):
        try:
            result = subprocess.run(["git", "--literal-pathspecs", *self.options, *args], cwd=self.root,
                                    env=self.environment, input=data, capture_output=True, timeout=20,
                                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        except subprocess.TimeoutExpired:
            raise ValueError("Git action timed out; its outcome may be unknown. Refresh before taking another action.") from None
        if result.returncode and not allow_failure:
            # Do not forward raw Git diagnostics: they may contain file content.
            raise ValueError("Git could not apply this action safely. Refresh Changes; conflicts or concurrent edits may need manual review.")
        return result

    def path(self, name):
        if not isinstance(name, str) or not name or "\\" in name or "\x00" in name:
            raise ValueError("Invalid repository path")
        path = PurePosixPath(name)
        if path.is_absolute() or any(p in {"..", ".git"} or ":" in p for p in path.parts):
            raise ValueError("Path is outside the review boundary")
        result = self.root
        for part in path.parts:
            result /= part
            if result.is_symlink() or (hasattr(result, "is_junction") and result.is_junction()):
                raise ValueError("Symlinks and junctions cannot be modified through Changes")
        if not result.resolve().is_relative_to(self.root):
            raise ValueError("Path is outside the review boundary")
        return result

    def content(self, name):
        path = self.path(name)
        if not path.exists():
            return None
        if not path.is_file() or path.stat().st_size > MAX_FILE:
            raise ValueError("Large or non-regular file: use an external editor")
        data = path.read_bytes()
        if len(data) > MAX_FILE or b"\0" in data:
            raise ValueError("Binary or large file: use an external editor")
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            raise ValueError("Non-UTF-8 file: use an external editor") from None
        return data

    def names(self):
        data = self.git("ls-files", "--cached", "--others", "--exclude-standard", "-z").stdout
        return sorted(set(n.decode("utf-8") for n in data.split(b"\0") if n))

    def changes(self):
        data = self.git("status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignore-submodules=all", "--no-renames").stdout
        return [(record[:2].decode(), record[3:].decode("utf-8")) for record in data.split(b"\0") if record]

    def head(self):
        return self.git("rev-parse", "--verify", "HEAD", allow_failure=True).stdout.decode().strip()

    def original(self, head, name):
        self.eligible(name)
        if not head:
            return None
        mode = self.git("ls-tree", "--format=%(objectmode)", head, "--", name).stdout.strip()
        if mode and mode not in {b"100644", b"100755"}:
            raise ValueError("Symlink/submodule baseline cannot be restored as a regular file")
        size = self.git("cat-file", "-s", f"{head}:{name}", allow_failure=True)
        if not size.returncode and int(size.stdout) > MAX_FILE:
            raise ValueError("Large baseline file")
        result = self.git("show", f"{head}:{name}", allow_failure=True)
        if result.returncode:
            return None
        data = result.stdout
        if len(data) > MAX_FILE or b"\0" in data:
            raise ValueError("Unsupported baseline file")
        data.decode("utf-8")
        # Stored Git blobs use LF; recover the normal working-tree convention
        # without invoking user-defined smudge/clean filters.
        eol = self.git("check-attr", "eol", "--", name).stdout.decode().strip().rsplit(": ", 1)[-1]
        crlf = self.git("config", "--get", "core.autocrlf", allow_failure=True).stdout.strip() == b"true"
        if (eol == "crlf" or (crlf and eol != "lf")) and b"\r\n" not in data:
            data = data.replace(b"\n", b"\r\n")
        return data

    def index(self):
        entries = self.git("ls-files", "--stage", "-z").stdout.split(b"\0")
        return {entry.split(b"\t", 1)[1].decode("utf-8"): entry.split(b"\t", 1)[0].decode("ascii") for entry in entries if entry}

    def signature(self, name, index):
        path = self.path(name)
        data = self.content(name)
        return {"hash": hashlib.sha256(data).hexdigest() if data is not None else None,
                "mode": stat.S_IMODE(path.stat().st_mode) if data is not None else None,
                "index": index.get(name)}

    def eligible(self, name):
        self.path(name)
        info = self.git("check-attr", "filter", "--", name).stdout.decode("utf-8").strip()
        if not info.endswith(": unspecified") and not info.endswith(": unset"):
            raise ValueError("Filtered files cannot be changed safely through this panel")


def quote_path(value):
    data = value.encode("utf-8")
    if all(33 <= ch < 127 and ch not in {34, 92} for ch in data):
        return value
    return '"' + "".join(chr(ch) if 32 <= ch < 127 and ch not in {34, 92} else f"\\{ch:03o}" for ch in data) + '"'


def patch(name, before, after, mode="100644"):
    if before == after:
        return ""
    a, b = quote_path("a/" + name), quote_path("b/" + name)
    header = f"diff --git {a} {b}\n"
    if before is None:
        header += f"new file mode {mode}\n"
    if after is None:
        header += f"deleted file mode {mode}\n"
    before_lines = (before or b"").decode("utf-8").splitlines(keepends=True)
    after_lines = (after or b"").decode("utf-8").splitlines(keepends=True)
    lines = list(difflib.unified_diff(before_lines, after_lines, fromfile="/dev/null" if before is None else a,
                                    tofile="/dev/null" if after is None else b, n=3))
    if not lines:  # Empty-file creation/deletion still needs a valid Git header.
        return header
    return header + "".join(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n" for line in lines)


def split_patch(value):
    lines = value.splitlines(keepends=True)
    starts = [i for i, line in enumerate(lines) if line.startswith("@@ ")]
    return ("".join(lines[:starts[0]]) if starts else value,
            ["".join(lines[a:b]) for a, b in zip(starts, [*starts[1:], len(lines)])])


def capture(repo, names=None):
    # Unchanged tracked files already have a local Git blob. Capture only the
    # working delta, not every file in a large repository on every message.
    names = sorted(name for _, name in repo.changes()) if names is None else sorted(names)
    files, skipped, total = {}, [], 0
    for i, name in enumerate(names):
        if i >= MAX_FILES:
            skipped.append(name)
            continue
        try:
            repo.eligible(name)
            data = repo.content(name)
            total += len(data or b"")
            if total > MAX_TOTAL:
                raise ValueError("Snapshot size limit")
            files[name] = base64.b64encode(data).decode("ascii") if data is not None else None
        except (ValueError, UnicodeDecodeError):
            skipped.append(name)
    return {"files": files, "skipped": skipped}


def pointer(repo, thread):
    key = hashlib.sha256(f"{repo.root}\0{thread}".encode()).hexdigest()
    return storage() / f"turn-{key}.json"


def preview(repo, view, thread, base=None):
    if view not in {"unstaged", "staged", "turn", "branch"}:
        raise ValueError("Unknown Changes view")
    index = repo.index()
    head = repo.head()
    notes, patches = [], {}
    bases = []
    comparison = None
    if view == "branch":
        bases = repo.git("for-each-ref", "--format=%(refname:short)", "refs/heads", "refs/remotes").stdout.decode().splitlines()
        base = base or next((candidate for candidate in ["origin/HEAD", "origin/main", "main", "origin/master", "master"] if candidate in bases), "HEAD")
        if not isinstance(base, str) or not re.fullmatch(r"[A-Za-z0-9_./-]{1,200}", base) or base.startswith("-"):
            raise ValueError("Choose a valid local branch/ref")
        resolved = repo.git("rev-parse", "--verify", f"{base}^{{commit}}", allow_failure=True)
        if resolved.returncode or not head:
            raise ValueError("The selected comparison branch has no local commit")
        common = repo.git("merge-base", head, resolved.stdout.decode().strip(), allow_failure=True)
        if common.returncode:
            raise ValueError("These branches have no common local history")
        comparison = common.stdout.decode().strip()
        notes.append(f"Committed changes since the common ancestor with {base}. Working edits are shown separately under Unstaged/Staged. No fetch or network request.")
    if view == "turn":
        source = pointer(repo, thread)
        if not source.exists():
            return {"token": "", "files": [], "view": view, "notes": ["No sealed turn snapshot for this chat yet. New turns capture a local baseline."]}
        latest = json.loads(source.read_text())
        if latest.get("pending"):
            return {"token": "", "files": [], "view": view, "notes": ["The latest turn has no sealed snapshot yet. Current Git changes remain reviewable; an older turn is not presented as the latest."]}
        snapshot = load(latest["snapshot"], "turn", repo.root)
        patches = snapshot["patches"]
        if snapshot["skipped"]:
            notes.append(f"{len(snapshot['skipped'])} binary, filtered, large or unsupported files were excluded from turn undo.")
        notes.append("Turn changes include all edits made during that run. Undo leaves the Git index unchanged.")
    else:
        records = [(" M", name.decode("utf-8")) for name in repo.git("diff", "--no-ext-diff", "--no-textconv", "--name-only", "--no-renames", "-z", comparison, head).stdout.split(b"\0") if name] if comparison else repo.changes()
        for code, name in records[:MAX_FILES]:
            if "U" in code or code in {"AA", "DD"}:
                notes.append(f"Conflict requires manual resolution: {name}")
                continue
            if (view == "staged" and code[0] in {" ", "?"}) or (view == "unstaged" and code[1] == " "):
                continue
            try:
                repo.eligible(name)
                repo.content(name)
                if code == "??":
                    value = patch(name, None, repo.content(name))
                else:
                    value = repo.git("diff", "--no-ext-diff", "--no-textconv", "--no-color", "--no-renames",
                                     *([comparison, head] if comparison else ["--cached"] if view == "staged" else []), "--", name).stdout.decode("utf-8")
                if len(value.encode()) > MAX_PATCH or "GIT binary patch" in value or "Binary files " in value:
                    raise ValueError("Binary or large diff: use an external editor")
                if value:
                    patches[name] = value
            except ValueError as exc:
                notes.append(f"{name}: {exc}")
        if len(records) > MAX_FILES:
            notes.append("Only the first 1,000 changed paths are shown; narrow this repository before batch actions.")
    files, signatures = [], {}
    for name, value in patches.items():
        try:
            if len(value.encode()) > MAX_PATCH:
                raise ValueError("Large diff: use an external editor; not silently truncated")
            repo.eligible(name)
            signatures[name] = repo.signature(name, index)
        except ValueError as exc:
            notes.append(f"{name}: {exc}")
            continue
        _, hunks = split_patch(value)
        files.append({"path": name, "hunks": len(hunks), "added": sum(l.startswith("+") and not l.startswith("+++") for l in value.splitlines()),
                      "removed": sum(l.startswith("-") and not l.startswith("---") for l in value.splitlines())})
    patches = {f["path"]: patches[f["path"]] for f in files}
    token = save({"kind": "review", "workspace": str(repo.root), "view": view, "head": head,
                  "signatures": signatures, "patches": patches, "created": time.time()})
    return {"token": token, "files": files, "view": view, "notes": notes, "base": base if view == "branch" else None, "bases": bases}


def operate(request):
    """Allowlisted UI/CLI operations. Snapshots never enter Git commits or prompts."""
    with review_lock():
        repo = Repository(request.get("workspace"))
        operation = request.get("operation")
        if operation == "capture":
            value = capture(repo)
            identity = save({"kind": "baseline", "workspace": str(repo.root), "head": repo.head(), "thread": str(request.get("thread_id") or ""), **value})
            pointer(repo, str(request.get("thread_id") or "")).write_text(json.dumps({"snapshot": identity, "pending": True}), encoding="utf-8")
            return {"snapshot": identity, "excluded": len(value["skipped"])}
        if operation == "seal":
            baseline = load(request.get("snapshot"), "baseline", repo.root)
            if repo.head() != baseline["head"]:
                raise ValueError("HEAD changed during the turn. Review the commit manually; turn Undo is unavailable.")
            after = capture(repo, {name for _, name in repo.changes()} | set(baseline["files"]) | set(baseline["skipped"]))
            patches = {}
            skipped = set(baseline["skipped"] + after["skipped"])
            for name in set(after["files"]) | set(baseline["files"]):
                if name in skipped:
                    continue
                decode = lambda val: base64.b64decode(val) if val is not None else None
                try:
                    before = decode(baseline["files"][name]) if name in baseline["files"] else repo.original(baseline["head"], name)
                except (ValueError, UnicodeDecodeError):
                    skipped.add(name)
                    continue
                ending = decode(after["files"].get(name))
                mode = "100644"
                if baseline["head"] and ending is None:
                    mode = repo.git("ls-tree", "--format=%(objectmode)", baseline["head"], "--", name).stdout.decode().strip() or mode
                value = patch(name, before, ending, mode)
                if value:
                    patches[name] = value
            identity = save({"kind": "turn", "workspace": str(repo.root), "patches": patches,
                             "skipped": sorted(skipped)})
            pointer(repo, baseline["thread"]).write_text(json.dumps({"snapshot": identity}), encoding="utf-8")
            return {"snapshot": identity, "files": len(patches)}
        if operation == "status":
            return preview(repo, request.get("view", "unstaged"), str(request.get("thread_id") or ""), request.get("base"))
        if operation == "recover":
            if request.get("confirmed") is not True:
                raise ValueError("Recovery requires explicit confirmation")
            recovery = load(request.get("recovery"), "recovery", repo.root)
            index = repo.index()
            if not recovery.get("after") or any(repo.signature(name, index) != expected for name, expected in recovery["after"].items()):
                raise ValueError("Files or index changed since that action. Keep the recovery copy and review manually; nothing was overwritten.")
            args = [*(["-c", "core.autocrlf=false"] if recovery["raw"] else []), "apply", "--whitespace=nowarn"]
            data = recovery["patch"].encode("utf-8")
            repo.git(*args, "--check", "-", data=data)
            repo.git(*args, "-", data=data)
            return {"ok": True, "message": "Recovered the files; Git index and chat history were not changed."}
        review = load(request.get("token"), "review", repo.root)
        if time.time() - review["created"] > 1800:
            raise ValueError("Review expired; refresh Changes")
        names = list(review["patches"]) if request.get("path") is None else [request["path"]]
        if not names or any(name not in review["patches"] for name in names):
            raise ValueError("Select a reviewed file first")
        index = repo.index()
        head = repo.head()
        if head != review["head"] or any(repo.signature(name, index) != review["signatures"][name] for name in names):
            raise ValueError("Files or the Git index changed after review. Refresh Changes; nothing was overwritten.")
        if operation == "file":
            if len(names) != 1:
                raise ValueError("Select one file")
            value = review["patches"][names[0]]
            return {"path": names[0], "patch": value}
        action = request.get("action")
        expected = {"stage": "unstaged", "unstage": "staged", "revert": "unstaged", "undo": "turn"}
        if operation != "apply" or action not in expected or review["view"] != expected[action] or request.get("confirmed") is not True:
            raise ValueError("This action needs explicit confirmation in its matching Changes view")
        values = [review["patches"][name] for name in names]
        hunk = request.get("hunk")
        if hunk is not None:
            if len(names) != 1 or not isinstance(hunk, int) or isinstance(hunk, bool):
                raise ValueError("Select one valid hunk")
            header, hunks = split_patch(values[0])
            if hunk < 0 or hunk >= len(hunks) or "file mode" in header:
                raise ValueError("This file needs a whole-file action")
            values = [header + hunks[hunk]]
        data = "".join(values).encode("utf-8")
        # Turn patches describe raw working bytes, not normalized index text.
        # Applying them with autocrlf conversion would reject or rewrite CRLF.
        args = [*(["-c", "core.autocrlf=false"] if action == "undo" else []), "apply", "--whitespace=nowarn", *(["--cached"] if action in {"stage", "unstage"} else []),
                *(["--reverse"] if action != "stage" else [])]
        repo.git(*args, "--check", "-", data=data)
        recovery = None
        saved = None
        if action in {"revert", "undo"}:
            backup = capture(repo, names)
            if backup["skipped"]:
                raise ValueError("Cannot retain a complete recovery copy for this batch. Select fewer files; nothing was changed.")
            saved = {"kind": "recovery", "workspace": str(repo.root), "patch": data.decode("utf-8"), "raw": action == "undo", **backup}
            recovery = save(saved)
        repo.git(*args, "-", data=data)
        if saved is not None:
            index_after = repo.index()
            saved["after"] = {name: repo.signature(name, index_after) for name in names}
            (storage() / f"{recovery}.json").write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")
        return {"ok": True, "files": len(names), "recovery": recovery,
                "message": "Action applied. Refresh to review the current files and index."}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Local Git review. No model or network requests; run write actions only while agent work is idle.")
    parser.add_argument("operation", choices=["status", "file", "capture", "seal", "apply", "recover"])
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--view", choices=["unstaged", "staged", "turn", "branch"], default="unstaged")
    parser.add_argument("--base", help="Local branch/ref for the read-only branch comparison; never fetches")
    parser.add_argument("--thread-id", default="")
    parser.add_argument("--token")
    parser.add_argument("--snapshot")
    parser.add_argument("--recovery")
    parser.add_argument("--path")
    parser.add_argument("--hunk", type=int)
    parser.add_argument("--action", choices=["stage", "unstage", "revert", "undo"])
    parser.add_argument("--yes", dest="confirmed", action="store_true", help="Confirm the exact reviewed write action")
    parser.add_argument("--json", action="store_true", help="Machine-readable output instead of the human review view")
    try:
        options = vars(parser.parse_args(argv))
        result = operate(options)
        if options["json"] or options["operation"] in {"capture", "seal"}:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif options["operation"] == "status":
            print(f"Changes · {result['view']} · {options['workspace']}")
            for file in result["files"]:
                print(f"  +{file['added']:<4} −{file['removed']:<4} {file['path']} ({file['hunks']} hunk(s))")
            if not result["files"]:
                print("  No reviewable changes.")
            for note in result["notes"]:
                print(f"  Note: {note}")
            if result["token"]:
                print(f"\nReview token: {result['token']}")
                print("Use changes file --token TOKEN --path FILE to inspect; write actions require --yes.")
        elif options["operation"] == "file":
            print(result["patch"], end="")
        else:
            print(result["message"])
            if result.get("recovery"):
                print(f"Recovery copy: {result['recovery']} (changes recover --recovery ID --yes)")
        return 0
    except (ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
