"""Does Smara remember? Replays one long synthetic owner history, then asks questions in fresh conversations.

    docker exec smara_app python -m evals.recall_eval --systems a,b,c --repeats 2 --json /tmp/recall.json --md /tmp/recall.md

The evaluation database is EVAL_DATABASE_URL, or else the app's DATABASE_URL with "_eval" added to the database name
(smara becomes smara_eval), so it can never point at the live database.

Three setups, each with its own shop, chat log and Continuum scopes, all with the real model and the real Continuum:
  a  as shipped: conversation log in the prompt + search_chat_history + Continuum lookup (facts written the way the app
     writes them: the per-turn memory pass, then the outbox)
  b  Continuum only: no chat history in the prompt and no search tool; every turn of the history is written to Continuum
  c  conversation log only: no Continuum (lookup, standing rules and the memory tools are off)
The questions are asked one at a time as new conversations, a few hours after the last message, and nothing a question
causes is kept. Scoring is by pattern (evals/recall_scenario.py), never by asking the model whether it was right.

Safety: it writes only to the database named in EVAL_DATABASE_URL, which must end in `_eval` (the schema there is dropped),
and it only ADDS records to Continuum, under scopes of shops that exist only in that database. It deletes nothing there.
The model key is shared with the live app (about 10 agent calls a minute): run it when nobody is chatting with Smara.
"""
import argparse
import asyncio
import importlib
import json
import os
import re
import sys
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx

from evals import recall_scenario as rs
from src import chatlog, tenants
from src.agent import memory_pass, prompt, tools
from src.bridge.continuum import ContinuumClient, make_source_id, scope_user
from src.bridge.outbox import drain
from src.box.sandbox import Sandbox
from src.config import Settings
from src.db import migrate, open_pool
from src.ingest.dispatch import dispatch
from src.ingest.importer import import_rows
from src.ingest.payload import InboundMessage
from src.ingest.sheet_mapper import SheetRow
from src.loop.llm import LLM

PHONES = {"a": "919000000011", "b": "919000000012", "c": "919000000013"}
SYSTEMS = {"a": "chat log + search + Continuum lookup (as shipped)",
           "b": "Continuum only (every turn written to it; no chat history, no search)",
           "c": "chat log + search only (no Continuum)"}
TELEGRAM_CHATS = {"a": 777000011, "b": 777000012, "c": 777000013}


class Fakes:
    """Where the owner's replies land, and a browser that is never used."""

    def __init__(self):
        self.sent: list[str] = []

    async def send(self, payload, **kw):                    # WhatsApp and Telegram share this fake
        body = payload.get("text") or ""
        self.sent.append(body.get("body", "") if isinstance(body, dict) else str(body))
        return f"wamid.eval{len(self.sent)}"

    async def upload_media(self, *a, **k):
        return "media.eval"

    async def send_document(self, *a, **k):
        return None

    async def answer(self, *a, **k):
        return None

    async def username(self):
        return "eval_bot"

    # browser stand-in
    def alive(self, sid): return False
    def page(self, sid): return {}
    async def step(self, *a, **k): return {"error": "no browser in the recall evaluation"}
    async def close(self, sid): return None
    async def close_all(self): return None


def eval_database_url(env) -> str:
    """EVAL_DATABASE_URL, or the app's database with `_eval` added to its name. Always a name that ends in `_eval`."""
    explicit = env.get("EVAL_DATABASE_URL", "")
    if explicit:
        return explicit
    live = env.get("DATABASE_URL", "")
    return re.sub(r"/([^/?]+)(\?.*)?$", lambda m: f"/{m.group(1)}_eval{m.group(2) or ''}", live) if live else ""


def check_db_url(url: str) -> str:
    name = url.rsplit("/", 1)[-1].split("?")[0]
    if not name.endswith("_eval"):
        sys.exit(f"refusing to run: EVAL_DATABASE_URL must name a database ending in _eval (got {name!r}); its schema is dropped")
    return url


async def build_shop(pool, label: str):
    t = await tenants.create(pool, name=f"Mehta Hardware ({label})", owner_phone=PHONES[label], upi_vpa="mehta@upi")
    async with pool.connection() as c:
        await c.execute("UPDATE tenants SET telegram_chat_id = %s, limits_exempt = true WHERE id = %s",
                        (TELEGRAM_CHATS[label], t.id))
    await import_rows(pool, t.id, [SheetRow(n, p, f"INV-{i + 1}", amt, (datetime.now().date() - timedelta(days=10 + i)))
                                   for i, (n, p, amt) in enumerate(rs.CUSTOMERS)])
    return await tenants.by_id(pool, t.id)


async def load_log(pool, tenant_id: str, turns, now: datetime) -> None:
    """The history as the app would have recorded it: one line per message, at its own time, on its own channel."""
    async with pool.connection() as c:
        for t in turns:
            at = now - timedelta(days=t.days_ago)
            for role, text, secs in (("owner", t.owner, 0), ("smara", t.smara, 20)):
                await c.execute("""INSERT INTO chat_messages (tenant_id, channel, role, content, created_at)
                                   VALUES (%s, %s, %s, %s, %s)""", (tenant_id, t.channel, role, text, at + timedelta(seconds=secs)))


async def teach_continuum_like_the_app(app, tenant, turns) -> int:
    """System a: the per-turn memory pass reads what the owner said and queues durable facts; the outbox delivers them."""
    n = 0
    for t in turns:
        if t.fact:                                  # chatter yields no facts; it is skipped to save model calls
            n += await memory_pass.remember_from_turn(app, tenant.id, str(uuid.uuid4()), [
                {"role": "system", "content": "x"}, {"role": "user", "content": t.owner},
                {"role": "assistant", "content": t.smara}])
    for _ in range(400):
        out = await drain(app.state.pool, app.state.continuum, datetime.now(timezone.utc), limit=100)
        async with app.state.pool.connection() as c:
            left = (await (await c.execute("SELECT count(*) AS n FROM continuum_outbox WHERE status = 'PENDING' AND user_id LIKE %s",
                                           (f"{tenant.id}%",))).fetchone())["n"]
        if not left:
            break
        await asyncio.sleep(1)
    return n


WRITE_GAP_S = 1.0           # a pause between writes: Continuum rate-limits (it answered 429 after ~50 quick writes)


async def write_with_retry(continuum, *, tries: int = 6, sleep=asyncio.sleep, **kw) -> None:
    """One write to Continuum, waiting and trying again when it says it is busy (429) or briefly unavailable (502/503)."""
    for attempt in range(tries):
        try:
            return await continuum.write_evidence(**kw)
        except httpx.HTTPStatusError as e:
            if e.response.status_code not in (429, 502, 503) or attempt == tries - 1:
                raise
            await sleep(float(e.response.headers.get("retry-after") or 0) or min(5 * 2 ** attempt, 60))


async def teach_continuum_every_turn(app, tenant, turns, batch: int = 4) -> int:
    """System b: the whole conversation goes into Continuum, a few turns per write (Continuum extracts facts in the
    background, with the same model key as everything else, so one write per message would take a long time)."""
    scope = scope_user(tenant.id, "shop")
    writes = 0
    for start in range(0, len(turns), batch):
        chunk = turns[start:start + batch]
        text = "\n\n".join(
            f"[{(datetime.now(timezone.utc) - timedelta(days=t.days_ago)).strftime('%d %b %Y')}, {t.channel}] Owner: {t.owner}\nSmara: {t.smara}"
            for t in chunk)
        await write_with_retry(app.state.continuum, user_id=scope, source_id=make_source_id(tenant.id, f"chunk{start}", text)[:200],
                               text=text, trusted=True, metadata={"actor": "owner", "via": "recall_eval"})
        await asyncio.sleep(WRITE_GAP_S)
        writes += 1
        if writes % 10 == 0:
            print(f"[b] {writes} writes to Continuum", flush=True)
    return writes


async def wait_until_searchable(app, tenant, probe_text: str, limit_s: int = 180) -> float:
    """Seconds until Continuum answers a question about a fact it was just given (it extracts in the background)."""
    started = time.monotonic()
    while time.monotonic() - started < limit_s:
        try:
            res = await app.state.continuum.resolve(user_id=scope_user(tenant.id, "shop"), query=probe_text, max_claims=3)
            card = ((res.get("resolution") or {}).get("state_card") or "")
            if card and prompt.EMPTY_CARD not in card:
                return round(time.monotonic() - started, 1)
        except Exception:
            pass
        await asyncio.sleep(3)
    return -1.0


class Patch:
    """Switch parts of the memory system off for one setup, and put them back."""

    def __init__(self, system: str):
        self.system, self.saved = system, []

    def _set(self, obj, name, value):
        if isinstance(obj, dict):
            self.saved.append((obj, name, obj[name]))
            obj[name] = value
        else:
            self.saved.append((obj, name, getattr(obj, name)))
            setattr(obj, name, value)

    async def _empty(self, *a, **k):
        return ""

    async def _zero(self, *a, **k):
        return 0

    def __enter__(self):
        import src.agent_loop as loop
        self._set(memory_pass, "remember_from_turn", self._zero)               # a question must not teach anything
        noop = _noop_write
        self._set(loop.EXECUTORS, "write_continuum_evidence", noop)
        drop = {"write_continuum_evidence"}
        if self.system == "b":
            self._set(chatlog, "recent", self._empty)
            self._set(chatlog, "related", self._empty)
            self._set(prompt, "owner_facts_block", self._empty)       # Continuum only: no stored facts either
            drop |= {"search_chat_history"}
        if self.system == "c":
            self._set(prompt, "standing_instructions", self._empty_standing)
            self._set(prompt, "relevant_memory", self._empty)
            self._set(prompt, "owner_facts_block", self._empty)
            drop |= {"resolve_continuum_memory"}
        self._set(tools, "TOOLS", [t for t in tools.TOOLS if t["function"]["name"] not in drop])
        return self

    async def _empty_standing(self, app, tenant):
        return ""

    def __exit__(self, *exc):
        for obj, name, value in reversed(self.saved):
            if isinstance(obj, dict):
                obj[name] = value
            else:
                setattr(obj, name, value)


async def _noop_write(app, ctx, args):
    return {"queued": True, "note": "not stored during the recall evaluation"}


async def ask(app, fakes, tenant, probe: rs.Probe, tokens: dict) -> dict:
    pool = app.state.pool
    t0 = datetime.now(timezone.utc) - timedelta(seconds=1)
    fakes.sent.clear()
    tokens["t"] = 0
    started = time.monotonic()
    err = ""
    try:
        await dispatch(app, InboundMessage(wamid=f"q-{uuid.uuid4()}", sender=tenant.owner_phone, kind="text",
                                           text=probe.question, channel=probe.channel))
    except Exception as e:                                           # a crashed run is a failed answer, not a stopped evaluation
        err = f"{type(e).__name__}: {e}"[:200]
    secs = round(time.monotonic() - started, 1)
    answers = [m for m in fakes.sent if m.strip() and not m.startswith("Still working on it")]
    reply = answers[-1] if answers else ""
    async with pool.connection() as c:
        sess = await (await c.execute("SELECT messages FROM agent_sessions WHERE tenant_id = %s AND created_at >= %s",
                                      (tenant.id, t0))).fetchall()
        calls = [call["function"]["name"] for s in sess for m in s["messages"] for call in (m.get("tool_calls") or [])]
        await c.execute("DELETE FROM agent_sessions WHERE tenant_id = %s AND created_at >= %s", (tenant.id, t0))
        await c.execute("DELETE FROM chat_messages WHERE tenant_id = %s AND created_at >= %s", (tenant.id, t0))
    verdict = rs.score(probe, reply)
    return {"probe": probe.id, "kind": probe.kind, "age_days": probe.age_days, "question": probe.question, "reply": reply,
            "ok": verdict["ok"], "why": err or verdict["why"], "secs": secs, "tokens": tokens["t"],
            "memory_tools": [c for c in calls if c in ("search_chat_history", "resolve_continuum_memory")]}


async def run_system(a, pool, llm, continuum, system: str) -> dict:
    fakes = Fakes()
    tokens = {"t": 0}
    real_chat = llm.chat

    async def counted(messages, tl, **kw):
        m = await real_chat(messages, tl, **kw)
        tokens["t"] += int((llm.last_usage or {}).get("total_tokens") or 0)
        return m
    llm.chat = counted
    app = SimpleNamespace(state=SimpleNamespace(pool=pool, settings=Settings(owner_files_dir=tempfile.mkdtemp(prefix="recall-")),
                                                wa=fakes, tg=fakes, bg=set(), continuum=continuum, browsers=fakes, llm=llm,
                                                sandbox=Sandbox(tempfile.mkdtemp(prefix="recall-sbx-"), "off"), agent_background=False))
    now = datetime.now(timezone.utc)
    turns = rs.history()
    tenant = await build_shop(pool, system)
    started = time.monotonic()
    info = {"system": system, "label": SYSTEMS[system], "turns": len(turns)}
    if system in ("a", "c"):
        await load_log(pool, tenant.id, turns, now)
    if system == "a":
        info["facts_written"] = await teach_continuum_like_the_app(app, tenant, turns)
    if system == "b":
        info["facts_written"] = await teach_continuum_every_turn(app, tenant, turns, a.batch)
    if system in ("a", "b"):
        info["searchable_after_s"] = await wait_until_searchable(app, tenant, rs.READY_QUERY)
    info["setup_secs"] = round(time.monotonic() - started)
    print(f"[{system}] ready ({info['setup_secs']}s; searchable after {info.get('searchable_after_s', 'n/a')}s)", flush=True)
    results = []
    with Patch(system):
        for rep in range(a.repeats):
            for p in rs.PROBES:
                if a.only and p.id not in a.only.split(","):
                    continue
                r = await ask(app, fakes, tenant, p, tokens)
                r["repeat"] = rep + 1
                results.append(r)
                print(f"[{system}] {'PASS' if r['ok'] else 'FAIL'} {p.id:<18} {r['secs']:>5}s tok={r['tokens']:<6} "
                      f"{r['why']} {('| ' + ' '.join(r['memory_tools'])) if r['memory_tools'] else ''}", flush=True)
                await asyncio.sleep(a.pause)
    llm.chat = real_chat
    return {**info, "results": results}


def summarise(out: dict) -> str:
    lines = ["# Recall evaluation", "", f"Model: `{out['model']}`. Scenario: {out.get('scenario', 'v1')}. Questions: {out['questions']} per setup, repeats: {out['repeats']}.", ""]
    lines += ["| Setup | Correct | Known facts | Unknown (does not invent) | Avg secs | Avg tokens |", "|---|---|---|---|---|---|"]
    for s in out["systems"]:
        r = s["results"]
        known, unk = [x for x in r if x["kind"] != "unknown"], [x for x in r if x["kind"] == "unknown"]
        pct = lambda xs: f"{100 * sum(x['ok'] for x in xs) / len(xs):.0f}% ({sum(x['ok'] for x in xs)}/{len(xs)})" if xs else "-"
        lines.append(f"| {s['system']}: {s['label']} | {pct(r)} | {pct(known)} | {pct(unk)} | "
                     f"{sum(x['secs'] for x in r) / max(len(r), 1):.0f} | {sum(x['tokens'] for x in r) / max(len(r), 1):.0f} |")
    kinds = sorted({x["kind"] for s in out["systems"] for x in s["results"]})
    lines += ["", "## By kind of question", "", "| Kind | " + " | ".join(s["system"] for s in out["systems"]) + " |",
              "|---|" + "---|" * len(out["systems"])]
    for k in kinds:
        cells = []
        for s in out["systems"]:
            xs = [x for x in s["results"] if x["kind"] == k]
            cells.append(f"{sum(x['ok'] for x in xs)}/{len(xs)}" if xs else "-")
        lines.append(f"| {k} | " + " | ".join(cells) + " |")
    lines += ["", "## By age of the fact", "", "| Age | " + " | ".join(s["system"] for s in out["systems"]) + " |", "|---|" + "---|" * len(out["systems"])]
    for name, lo, hi in (("today", 0, 1), ("2 to 12 days", 1, 14), ("14 to 39 days", 14, 40), ("40 days or more", 40, 999)):
        cells = []
        for s in out["systems"]:
            xs = [x for x in s["results"] if x["kind"] != "unknown" and lo <= x["age_days"] < hi]
            cells.append(f"{sum(x['ok'] for x in xs)}/{len(xs)}" if xs else "-")
        lines.append(f"| {name} | " + " | ".join(cells) + " |")
    lines += ["", "## Every question", "", "| Question | " + " | ".join(s["system"] for s in out["systems"]) + " |", "|---|" + "---|" * len(out["systems"])]
    for p in rs.PROBES:
        cells = []
        for s in out["systems"]:
            xs = [x for x in s["results"] if x["probe"] == p.id]
            cells.append(("pass" if all(x["ok"] for x in xs) else ("; ".join(sorted({x["why"] for x in xs if not x["ok"]})) or "fail")) if xs else "-")
        lines.append(f"| {p.question} | " + " | ".join(cells) + " |")
    lines += ["", "## Setup", ""] + [f"- {s['system']}: {s['setup_secs']}s to load; Continuum searchable after {s.get('searchable_after_s', 'n/a')}s; "
                                    f"{s.get('facts_written', 0)} writes to Continuum" for s in out["systems"]]
    return "\n".join(lines) + "\n"


SCENARIOS = {"v1": "evals.recall_scenario", "v2": "evals.recall_scenario_v2"}


async def main() -> None:
    global rs
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default="v1", choices=sorted(SCENARIOS),
                    help="v1 is the history the memory code was developed against; v2 was written afterwards and not tuned for")
    ap.add_argument("--systems", default="a,b,c")
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--only", default="", help="comma-separated probe ids")
    ap.add_argument("--batch", type=int, default=3, help="turns per write to Continuum for setup b")
    ap.add_argument("--pause", type=float, default=4.0, help="seconds between questions, to leave the shared model key some room")
    ap.add_argument("--json", default="")
    ap.add_argument("--md", default="")
    a = ap.parse_args()
    rs = importlib.import_module(SCENARIOS[a.scenario])
    url = check_db_url(eval_database_url(os.environ))
    pool = await open_pool(url)
    async with pool.connection() as c:
        await c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
    await migrate(pool)
    model = os.environ.get("LLM_AGENT_MODEL") or os.environ.get("LLM_MODEL", "")
    llm = LLM(os.environ["LLM_BASE_URL"], os.environ["LLM_API_KEY"], model, agent_model=model,
              agent_rpm=float(os.environ.get("LLM_AGENT_RPM", "0")), fallback_model=os.environ.get("LLM_FALLBACK_MODEL", ""))
    continuum = ContinuumClient(os.environ["CONTINUUM_URL"], os.environ["CONTINUUM_API_KEY"])
    systems = []
    for s in a.systems.split(","):
        systems.append(await run_system(a, pool, llm, continuum, s.strip()))
    out = {"model": model, "scenario": a.scenario, "repeats": a.repeats, "questions": len([p for p in rs.PROBES if not a.only or p.id in a.only.split(",")]),
           "systems": systems}
    if a.json:
        Path(a.json).write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    text = summarise(out)
    if a.md:
        Path(a.md).write_text(text, encoding="utf-8")
    print("\n" + text)
    await llm.aclose()
    await continuum.aclose()
    await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
