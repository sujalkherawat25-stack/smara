"""Runs the Smara wholesale benchmark (a folder with data/, questions.json, _private/truth.json, tools/score.py) through the real agent.

Every file is stored as the owner's own earlier upload (owner_files), the ledger is emptied so only the files hold data, and each question
is asked as a normal chat message. The reply is parsed as JSON and scored with the benchmark's own scorer.

  python -u -m evals.wholesale_bench --db <scratch _eval db> --model glm5.3-flash --sandbox container --jobs /jobs \
      --bench /bench --out /out [--only R1,R2]
"""
import argparse
import asyncio
import json
import re
import selectors
import sys
from pathlib import Path

from evals import agent_harness as h
from evals import demo_scenarios as ds
from evals.agent_harness import Task, owner

from src.ingest import owner_files

_ap = argparse.ArgumentParser(add_help=False)
_ap.add_argument("--bench", default="/bench")
_ap.add_argument("--out", default="/out")
_known, _rest = _ap.parse_known_args()
sys.argv = [sys.argv[0]] + _rest
BENCH, OUT = Path(_known.bench), Path(_known.out)
sys.path.insert(0, str(BENCH / "tools"))
import score as sc  # noqa: E402

QS = json.loads((BENCH / "questions.json").read_text(encoding="utf-8"))
TRUTH = json.loads((BENCH / "_private" / "truth.json").read_text(encoding="utf-8"))
ANSWERS: dict = {}


def parse(text: str, kind: str):
    """The first JSON value in the reply (a long answer may be split over several messages; they are joined)."""
    text = text.replace("*", "")
    dec = json.JSONDecoder()
    for m in re.finditer(r"[\[{]", text):
        try:
            return dec.raw_decode(text[m.start():])[0]
        except ValueError:
            continue
    if kind == "number":
        m = re.search(r"-?\d[\d,]*\.?\d*", text)
        return float(m.group(0).replace(",", "")) if m else None
    return None


def all_text(env) -> str:
    return "\n".join(m.get("text", {}).get("body", "") or m.get("interactive", {}).get("body", {}).get("text", "") for m in env.wa.to(ds.OWNER))


async def ensure_files(env, names):
    from src import tenants
    t = await tenants.by_owner_phone(env.pool, ds.OWNER)
    base = env.app.state.settings.owner_files_dir
    from datetime import datetime, timezone
    have = {f["name"] for f in owner_files.live_files(base, t.id, datetime.now(timezone.utc))}
    for n in names:
        if n not in have:
            owner_files.save(base, t.id, n, (BENCH / "data" / n).read_bytes())


def make(q):
    async def ev(env):
        await ds._clear_ledger(env)
        await ensure_files(env, q["files"])
        # the files are already the owner's earlier uploads; the question names them the way a person would
        await h.dispatch(env.app, owner(q["prompt"], f"wb-{q['id']}"))

    async def chk(env):
        ans = parse(all_text(env), q["kind"])
        ANSWERS[q["id"]] = ans
        rows = await h.q(env, "select messages from agent_sessions order by created_at")
        code = []
        for r in rows:
            for m in r["messages"]:
                for c in m.get("tool_calls") or []:
                    code.append("# " + c["function"]["name"] + chr(10) + str(c["function"]["arguments"]))
                if m.get("role") == "tool":
                    code.append("# -> " + str(m.get("content"))[:600])
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / f"trace_{q['id']}.txt").write_text(chr(10).join(code), encoding="utf-8")
        s = sc.score_one(q, ans, TRUTH[q["truth_key"]])
        return s >= 0.999, f"score={s:.2f} answer={str(ans)[:160]!r}"
    return Task(f"{q['id']}_{q['category'].split()[0].lower()}", ("custom", ev), chk)


async def _main():
    h.TASKS[:] = [make(q) for q in QS]
    try:
        await h.main()
    finally:
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "answers.json").write_text(json.dumps(ANSWERS, indent=1, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    h.seed = ds.seed
    h.run_task = ds.run_task
    # --only takes question ids (R1,R5); the harness filters by task name, so map ids to names
    for i, a in enumerate(sys.argv):
        if a == "--only" and i + 1 < len(sys.argv):
            want = set(sys.argv[i + 1].split(","))
            sys.argv[i + 1] = ",".join(f"{q['id']}_{q['category'].split()[0].lower()}" for q in QS if q["id"] in want)
    asyncio.run(_main(), loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))
