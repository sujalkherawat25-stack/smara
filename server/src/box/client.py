import asyncio
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _run(tool: str, args: dict, timeout: int) -> dict:
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "src.box.worker", tool, json.dumps(args)],
            capture_output=True, text=True, timeout=timeout, cwd=ROOT)
    except subprocess.TimeoutExpired:
        return {"error": f"timed out after {timeout}s"}
    lines = proc.stdout.strip().splitlines()
    try:
        return json.loads(lines[-1])
    except (IndexError, ValueError):
        return {"error": (proc.stderr or "worker produced no output")[-300:]}


async def run_box(tool: str, args: dict, timeout: int = 90) -> dict:
    # Thread, not asyncio subprocess: the Selector loop psycopg needs on Windows can't spawn processes.
    return await asyncio.to_thread(_run, tool, args, timeout)
