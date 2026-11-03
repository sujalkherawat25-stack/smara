"""Sandbox runner: executes the agent's Python on uploaded files, in isolation.

Production runs this in its own container (`munim_sandbox`): no network, read-only
root, tmpfs /tmp, memory/CPU/pid limits. Jobs arrive as directories in the shared
/jobs volume (root-only, 0700). Each job is copied into a private tmp dir owned by
`nobody` and run as `nobody` with rlimits and a wall-clock timeout, so a job can
read only its own inputs. Outputs (./out, capped) and stdout come back as
result.json. Locally (dev/tests) the same `execute` runs without the container.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
MAX_OUT_FILES, MAX_OUT_BYTES, MAX_STREAM = 10, 10_000_000, 20_000
WRAPPER = '''import json, os, sys, traceback
sys.path.insert(0, os.getcwd())   # -I drops the script dir; helpers live here, inputs stay in ./in
g = {"__name__": "__main__"}
try:
    exec(compile(open("user_code.py", encoding="utf-8").read(), "user_code.py", "exec"), g)
except SystemExit:
    pass
except Exception:
    traceback.print_exc()
    sys.exit(1)
finally:
    if "RESULT" in g:
        try:
            json.dump(g["RESULT"], open("out/__result__.json", "w", encoding="utf-8"), default=str)
        except Exception as e:
            print("RESULT is not JSON-serializable:", e, file=sys.stderr)
'''


def _limits(cpu_s: int):
    def apply():
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_s, cpu_s))
        resource.setrlimit(resource.RLIMIT_AS, (1_500_000_000, 1_500_000_000))
        resource.setrlimit(resource.RLIMIT_FSIZE, (50_000_000, 50_000_000))
        resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
        resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))   # applies to nobody once uid drops
        if os.getuid() == 0:            # drop to nobody before user code runs
            os.setgroups([])
            os.setgid(65534)
            os.setuid(65534)
    return apply


def execute(job_dir: Path, *, isolate: bool = True) -> dict:
    spec = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    timeout = int(spec.get("timeout_s", 60))
    work = Path(tempfile.mkdtemp(prefix="run-"))
    try:
        shutil.copytree(job_dir / "in", work / "in")
        (work / "out").mkdir()
        shutil.copy(job_dir / "code.py", work / "user_code.py")
        shutil.copy(HERE / "munim_helpers.py", work / "munim_helpers.py")
        (work / "_run.py").write_text(WRAPPER, encoding="utf-8")
        posix = os.name == "posix"
        if posix and isolate and os.getuid() == 0:
            for p in [work, *work.rglob("*")]:
                os.chown(p, 65534, 65534)
        env = {"PATH": os.environ.get("PATH", ""), "HOME": str(work), "MPLBACKEND": "Agg",
               "OPENBLAS_NUM_THREADS": "1", "PYTHONIOENCODING": "utf-8"}
        if not posix:
            env["SYSTEMROOT"] = os.environ.get("SYSTEMROOT", "")
        started, timed_out = time.monotonic(), False
        try:
            proc = subprocess.run([sys.executable, "-I", "-B", "_run.py"], cwd=work, env=env, capture_output=True,
                                  timeout=timeout, preexec_fn=_limits(timeout) if posix and isolate else None)
            code, out, err = proc.returncode, proc.stdout, proc.stderr
        except subprocess.TimeoutExpired as e:
            timed_out, code, out, err = True, -9, e.stdout or b"", e.stderr or b""
        outputs, total = [], 0
        (job_dir / "out").mkdir(exist_ok=True)
        for f in sorted((work / "out").iterdir()):
            if f.is_file() and f.name != "__result__.json" and len(outputs) < MAX_OUT_FILES:
                total += f.stat().st_size
                if total <= MAX_OUT_BYTES:
                    shutil.copy(f, job_dir / "out" / f.name)
                    outputs.append(f.name)
        result_file = work / "out" / "__result__.json"
        result = {
            "exit_code": code, "timed_out": timed_out, "seconds": round(time.monotonic() - started, 2),
            "stdout": out.decode("utf-8", "replace")[-MAX_STREAM:], "stderr": err.decode("utf-8", "replace")[-MAX_STREAM:],
            "outputs": outputs,
            "result": json.loads(result_file.read_text(encoding="utf-8"))
            if result_file.exists() and result_file.stat().st_size < 1_000_000 else None,
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)
    tmp = job_dir / "result.json.tmp"
    tmp.write_text(json.dumps(result, default=str), encoding="utf-8")
    tmp.replace(job_dir / "result.json")
    return result


def serve(jobs: Path) -> None:
    """Container entrypoint: claim ready jobs one at a time (mkdir is the atomic lock)."""
    os.chmod(jobs, 0o711)                   # nobody may traverse but not list /jobs
    while True:
        (jobs / ".heartbeat").write_text(str(time.time()))
        for d in sorted(p for p in jobs.iterdir() if p.is_dir()):
            if (d / "ready").exists() and not (d / "result.json").exists():
                try:
                    (d / "taken").mkdir()
                except FileExistsError:
                    continue
                try:
                    execute(d)
                except Exception as e:   # never let one bad job stop the runner
                    (d / "result.json").write_text(json.dumps({"exit_code": -1, "stderr": f"runner error: {e}"}))
        time.sleep(0.2)


if __name__ == "__main__":
    serve(Path(os.environ.get("SANDBOX_JOBS", "/jobs")))
