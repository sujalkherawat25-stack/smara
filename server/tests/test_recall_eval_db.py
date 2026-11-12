"""The recall evaluation's runner, with a fake model and a fake Continuum: the plumbing, not Smara's memory."""
import json
import os
import re
from argparse import Namespace

import pytest

from evals import recall_eval as ev
from evals import recall_scenario as rs
from src import chatlog
from src.agent import tools
from src.db import migrate, open_pool

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")
STOP = {"kitna", "kahan", "kaun", "kya", "tha", "hain", "hai", "kab", "bhejna", "dekhta", "mein", "humara", "ka", "ke", "ki", "ko"}


def words(text):
    return {w for w in re.findall(r"[a-z0-9]{3,}", text.lower()) if w not in STOP}


class FakeContinuum:
    def __init__(self):
        self.rows = []                                    # (user_id, text)

    async def write_evidence(self, *, user_id, source_id, text, trusted, metadata=None):
        self.rows.append((user_id, text))

    async def resolve(self, *, user_id, query, max_claims=8):
        hits = [t for u, t in self.rows if u == user_id and len(words(t) & words(query)) >= 2][:max_claims]
        if not hits:
            return {"resolution": {"state_card": "No relevant state was found in the retrieved evidence."}}
        card = "RETRIEVED EVIDENCE (no temporal state inferred)\n" + "\n".join(
            f"[UNVERSIONED EVIDENCE] [TRUSTED] {h} (source=x_{i}, turn=turn:1, valid_at=2026-11-12T00:00:00+00:00)" for i, h in enumerate(hits))
        return {"resolution": {"state_card": card}}

    async def aclose(self):
        pass


class FakeLLM:
    """Searches the chat when it can, then answers with the line of its context that shares most words with the question."""
    enabled = True
    agent_model = "fake"
    last_usage = {"total_tokens": 10}
    stats = {}

    def __init__(self):
        self.tool_sets, self.systems = [], []

    async def json(self, system, user, **kw):
        owner = user.split("Owner wrote:\n", 1)[1].split("\n\nAssistant replied", 1)[0]
        return {"facts": [{"about": "shop", "customer": "", "fact": owner}]}

    async def chat(self, messages, tl, allow_fallback=True):
        names = {t["function"]["name"] for t in tl}
        self.tool_sets.append(names)
        self.systems.append(messages[0]["content"])
        question = next(m["content"] for m in reversed(messages) if m["role"] == "user")
        if "search_chat_history" in names and not any(m["role"] == "tool" for m in messages):
            return {"content": "", "tool_calls": [{"id": "c1", "type": "function", "function": {
                "name": "search_chat_history", "arguments": json.dumps({"query": question, "limit": 15})}}]}
        context = [l for m in messages if m["role"] in ("system", "tool") for l in str(m["content"]).splitlines()]
        best = max(context, key=lambda l: len(words(l) & words(question)), default="")
        return {"content": best if len(words(best) & words(question)) >= 2 else "Mujhe nahi pata."}

    async def aclose(self):
        pass


@pytest.fixture
async def pool():
    p = await open_pool(URL)
    async with p.connection() as c:
        await c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
    await migrate(p)
    yield p
    await p.close()


def args():
    return Namespace(repeats=1, only="", pause=0, batch=4)


async def test_each_setup_runs_cleans_up_after_every_question_and_restores_what_it_switched_off(pool):
    full_tools = list(tools.TOOLS)
    recent = chatlog.recent
    llm, mem = FakeLLM(), FakeContinuum()
    out = {}
    for system in ("a", "b", "c"):
        llm.tool_sets.clear(), llm.systems.clear()
        out[system] = await ev.run_system(args(), pool, llm, mem, system)
        names = set().union(*llm.tool_sets)
        res = out[system]["results"]
        assert len(res) == len(rs.PROBES) and all(r["reply"] for r in res), system
        assert not any(r["why"].startswith(("Runtime", "Type", "Attribute", "Key", "Value")) for r in res), [r["why"] for r in res]
        assert "write_continuum_evidence" not in names                                 # a question never teaches anything
        assert (names >= {"search_chat_history"}) == (system != "b")                   # b has no search tool
        assert (names >= {"resolve_continuum_memory"}) == (system != "c")              # c has no Continuum tool
        history_in_prompt = any("Earlier chats with the owner" in s for s in llm.systems)
        assert history_in_prompt == (system != "b")                                    # b sees no chat history
        assert any("Memory about this message" in s for s in llm.systems) == (system in ("a", "b") and bool(mem.rows))
        async with pool.connection() as c:
            left = (await (await c.execute("SELECT count(*) AS n FROM agent_sessions")).fetchone())["n"]
        assert left == 0                                                               # no question leaves a conversation behind
    assert tools.TOOLS == full_tools and chatlog.recent is recent                      # everything put back
    async with pool.connection() as c:
        per_shop = {r["name"][-2]: r["n"] for r in await (await c.execute(
            """SELECT t.name, count(m.*) AS n FROM tenants t LEFT JOIN chat_messages m ON m.tenant_id = t.id GROUP BY t.name""")).fetchall()}
    assert per_shop == {"a": 2 * len(rs.history()), "b": 0, "c": 2 * len(rs.history())}   # only the history, none of the questions
    assert out["a"]["facts_written"] == len(rs.FACTS) and out["b"]["facts_written"] == -(-len(rs.history()) // 4)
    assert any(u.endswith("_shop") for u, _ in mem.rows)


async def test_the_summary_reports_each_setup_by_kind_and_age(pool):
    fake = {"model": "fake", "repeats": 1, "questions": len(rs.PROBES), "systems": [
        {"system": "a", "label": ev.SYSTEMS["a"], "setup_secs": 5, "searchable_after_s": 3.0, "facts_written": 13,
         "results": [{"probe": p.id, "kind": p.kind, "age_days": p.age_days, "ok": i % 2 == 0, "why": "" if i % 2 == 0 else "missing: x",
                      "secs": 4, "tokens": 900} for i, p in enumerate(rs.PROBES)]}]}
    text = ev.summarise(fake)
    for part in ("| Setup |", "By kind of question", "By age of the fact", "Every question", "Humara CA kaun hai?", "| unknown |"):
        assert part in text


def test_the_evaluation_database_is_derived_from_the_live_one_and_never_equals_it():
    live = "postgresql://smara:pw@smara_postgres:5432/smara"
    assert ev.eval_database_url({"DATABASE_URL": live}) == "postgresql://smara:pw@smara_postgres:5432/smara_eval"
    assert ev.eval_database_url({"DATABASE_URL": live + "?sslmode=disable"}).endswith("/smara_eval?sslmode=disable")
    assert ev.eval_database_url({"DATABASE_URL": live, "EVAL_DATABASE_URL": "postgresql://x/other_eval"}) == "postgresql://x/other_eval"
    assert ev.eval_database_url({}) == ""
    with pytest.raises(SystemExit):
        ev.check_db_url(ev.eval_database_url({}))


def test_the_runner_refuses_a_database_that_is_not_an_eval_database():
    with pytest.raises(SystemExit):
        ev.check_db_url("postgresql://smara:x@db:5432/smara")
    assert ev.check_db_url("postgresql://smara:x@db:5432/smara_eval") == "postgresql://smara:x@db:5432/smara_eval"


async def test_the_runner_runs_the_second_scenario_too(pool, monkeypatch):
    from evals import recall_scenario_v2 as v2
    monkeypatch.setattr(ev, "rs", v2)
    out = await ev.run_system(args(), pool, FakeLLM(), FakeContinuum(), "a")
    assert [r["probe"] for r in out["results"]] == [p.id for p in v2.PROBES]
    assert all(r["reply"] for r in out["results"])
    assert out["facts_written"] == len(v2.FACTS) and out["turns"] == len(v2.history())
    assert "Scenario: v2" in ev.summarise({"model": "fake", "scenario": "v2", "repeats": 1, "questions": len(v2.PROBES),
                                          "systems": [out]})
    assert "Scenario: v1" in ev.summarise({"model": "fake", "repeats": 1, "questions": 0, "systems": []})   # older reports have no name


def test_the_scenario_flag_names_two_scenarios():
    assert set(ev.SCENARIOS) == {"v1", "v2"}
