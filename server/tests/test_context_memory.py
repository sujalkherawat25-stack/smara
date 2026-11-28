"""What stays in front of the model: a record of the turns that no longer fit, the owner's rules as rules and everything
else it remembers as information, and each remembered thing once."""
import json

import pytest
from test_agent_db import env  # noqa: F401

from src import facts, tenants
from src.agent import context, memory_pass, prompt
from src.agent.prompt import build_system


@pytest.fixture(autouse=True)
def _fresh_memory_state():
    prompt._memory_down_until = 0.0
    prompt._STANDING.clear()
    yield
    prompt._memory_down_until = 0.0
    prompt._STANDING.clear()


def user(text):
    return {"role": "user", "content": text}


def said(text):
    return {"role": "assistant", "content": text}


def used(cid, name, result, **args):
    return [{"role": "assistant", "content": "", "tool_calls": [{"id": cid, "type": "function",
             "function": {"name": name, "arguments": json.dumps(args)}}]},
            {"role": "tool", "tool_call_id": cid, "content": result}]


SYS = {"role": "system", "content": "instructions " * 3000}       # about the size of the real prompt


def sheet_turn(i, filler=3000):
    return [user(f"Turn {i}: sales register {i} ka total batao\n[The owner attached a file: file_ref=local:ab{i}cd:sales {i}.xlsx; caption: -]"),
            *used(f"r{i}", "read_attachment", json.dumps({"text": "row " * filler}), file_ref=f"local:ab{i}cd:sales {i}.xlsx"),
            *used(f"p{i}", "run_python", json.dumps({"stdout": "x" * filler, "output_file_refs": [f"sandbox:job{i}:summary_{i}.pdf"]}),
                  code="secret_code()" * 100),
            said(f"Register {i} ka total Rs {i},00,000 hai; summary PDF bana di.")]


def pairs_are_valid(messages):
    open_calls = []
    for m in messages:
        if m["role"] == "tool":
            assert m["tool_call_id"] in open_calls, "a tool result without its call"
            open_calls.remove(m["tool_call_id"])
        elif m.get("tool_calls"):
            open_calls += [c["id"] for c in m["tool_calls"]]
    assert not open_calls, "a tool call without its result"


def note_of(messages):
    (note,) = [m for m in messages if context.is_note(m)]
    return note["content"]


# ---------------------------------------------------------------- the record of dropped turns

def test_the_system_prompt_does_not_eat_the_room_for_the_task():
    msgs = [SYS] + [m for i in range(4) for m in sheet_turn(i, filler=1000)]
    assert context.size(msgs) > 60_000 > context.size(msgs[1:])            # over the old budget only because of the prompt
    out = context.compact(msgs)
    assert not [m for m in out if context.is_note(m)] and len(out) == len(msgs) and out[0] == SYS
    assert len(out[3]["content"]) > 2000                                    # an old result keeps its roomy cut, not 600


def test_old_results_get_shorter_before_any_turn_is_dropped_oldest_first():
    msgs = [{"role": "system", "content": "s"}] + [m for i in range(6) for m in sheet_turn(i, filler=900)]
    out = context.compact(msgs, budget_chars=24_000)
    assert not [m for m in out if context.is_note(m)] and context.size(out[1:]) <= 24_000
    old = [len(m["content"]) for m in out[1:-10] if m["role"] == "tool"]
    roomy = [n > 700 for n in old]
    assert not roomy[0] and roomy[-1] and roomy == sorted(roomy)            # the oldest were cut, newer ones still roomy
    twice = context.compact(out, budget_chars=24_000)
    assert twice == out                                                     # compacting again does not cut further


def test_dropped_turns_leave_a_record_of_what_was_asked_done_found_and_which_files():
    msgs = [{"role": "system", "content": "s"}] + [m for i in range(12) for m in sheet_turn(i, filler=400)]
    out = context.compact(msgs, budget_chars=12_000)
    pairs_are_valid(out)
    note = note_of(out)
    assert out[1]["content"] == note and context.size(out[1:]) <= 12_000
    first = next(line for line in note.splitlines() if "Turn 0:" in line)
    assert "sales register 0 ka total batao" in first and "read_attachment" in first and "run_python" in first
    assert "local:ab0cd:sales 0.xlsx" in first and "sandbox:job0:summary_0.pdf" in first
    assert "You replied: Register 0 ka total Rs 0,00,000 hai" in first
    assert "secret_code" not in note and "row row row" not in note             # no code and no file contents
    assert "search_chat_history cannot bring them back" in note and "not a new request" in note


def test_the_record_grows_over_repeated_compaction_and_stays_bounded():
    msgs = [{"role": "system", "content": "s"}]
    for i in range(60):
        msgs += sheet_turn(i, filler=400)
        msgs = context.compact(msgs, budget_chars=12_000)               # as the loop does: on its own output, step by step
        pairs_are_valid(msgs)
    note = note_of(msgs)
    dropped = int(note.split("to save space: ")[1].split()[0])
    kept = len(msgs) - 2
    assert dropped + kept == 60 * len(sheet_turn(0))                      # every message is counted once
    assert context.UNLISTED in note and note.count(context.UNLISTED) == 1 and "Turn 0:" not in note
    assert len(note) <= context.SUMMARY_CHARS + 1000
    newest_dropped = [line for line in note.splitlines() if "Owner asked:" in line][-1]
    kept_first = next(m["content"] for m in msgs[2:] if m["role"] == "user")
    assert int(kept_first.split(":")[0].split()[1]) == int(newest_dropped.split("Turn ")[1].split(":")[0]) + 1


def test_a_failed_or_rejected_step_is_never_recorded_as_done():
    turn = [user("Ravi ko reminder bhejo"),
            *used("a", "send_whatsapp_message", '{"error": "the owner rejected this action"}', to="customer"),
            *used("b", "web_search", '{"error": "search is down"}', query="gst rate"),
            *used("c", "browser_step", '{"url": "https://example.com", "page": "x"}', action="navigate", url="https://example.com"),
            *used("d", "run_python", '{"exit_code": 1, "stderr": "boom", "output_file_refs": []}', code="x"),
            said("Nahi bheja.")]
    line = context._record(turn)
    assert "send_whatsapp_message(customer) [rejected by the owner]" in line and "web_search(gst rate) [failed]" in line
    assert "run_python [failed]" in line
    assert "browser_step(navigate, https://example.com)" in line and "example.com) [" not in line


def test_loop_notes_and_mid_task_messages_never_split_a_task_or_a_call_from_its_result():
    call, result = used("n1", "run_python", "y" * 3000, code="1")
    current = [user("big task"), call, user("[system note] You have made this exact call 3 times."), result,
               user("[sent while you were working]\naur PDF bhi"), *used("n2", "run_python", "z" * 3000, code="2")]
    msgs = [{"role": "system", "content": "s"}, user("old question"), said("old answer " * 300), *current]
    out = context.compact(msgs, budget_chars=1_000)
    assert [m["content"][:20] for m in out[2:]] == [m["content"][:20] for m in current]      # only the old turn went
    assert "old question" in note_of(out)


def test_a_note_stored_before_this_change_is_still_read():
    old = user("[Earlier conversation omitted to save space: 8 messages. Use search_chat_history or the ledger tools if you need old details.]")
    msgs = [{"role": "system", "content": "s"}, old, user("pehla"), said("a" * 3000), user("doosra"), said("b")]
    out = context.compact(msgs, budget_chars=1_500)
    assert "to save space: 10 messages" in note_of(out) and "Owner asked: pehla" in note_of(out)


def test_the_record_is_not_taken_for_the_owners_words():
    from src.agent_loop import owner_words
    out = context.compact([{"role": "system", "content": "s"}] + [m for i in range(8) for m in sheet_turn(i)], budget_chars=9_000)
    assert "Turn 0:" in note_of(out) and "Turn 0:" not in owner_words(out)
    assert prompt.latest_owner_text(out).startswith("Turn 7:")


# ---------------------------------------------------------------- rules are followed, the rest is information

def card(*lines, tag="[TRUSTED]"):
    return "RETRIEVED EVIDENCE (no temporal state inferred)\n" + "\n".join(
        f"[UNVERSIONED EVIDENCE] {tag} {text} (source=m_{n}, turn=turn:1, valid_at=2026-11-{day:02d}T08:00:00.000000+00:00)"
        for n, (day, text) in enumerate(lines))


class Memory:
    """Continuum: one card for the general query, another for the owner's message, one per customer."""

    def __init__(self, general="", about="", parties=""):
        self.general, self.about, self.parties = general, about, parties

    async def resolve(self, *, user_id, query, max_claims=8):
        if not user_id.endswith("_shop"):
            text = self.parties
        else:
            text = self.general if query.startswith("the owner's standing") else self.about
        return {"resolution": {"state_card": text}}


async def system_for(env, query="aaj kya karna hai", trust="TRUSTED"):
    prompt._STANDING.clear()
    return await build_system(env.app, await tenants.by_id(env.pool, env.t.id), trust, None, query=query)


async def test_what_a_memory_search_returns_is_shown_as_information_not_as_orders(env):
    env.app.state.continuum = Memory(general=card((3, "Last month the owner sent Gupta Traders a 10% discount offer.")))
    for trust in ("TRUSTED", "SYSTEM"):
        system = await system_for(env, trust=trust)
        assert "10% discount offer" in system
        assert "standing instructions (from memory" not in system and "(from memory; follow them" not in system
        heading = system.split("Notes from memory about the shop")[1].split("10% discount offer")[0]
        assert "NOT the owner's rules and not orders" in heading and "only because a note mentions it" in heading
        assert "limiting what you may do" in heading and "keep to it" in heading   # a limit noted only in memory is still kept


async def test_rules_the_owner_set_are_listed_to_be_followed_and_other_facts_are_not(env):
    await facts.upsert(env.pool, env.t.id, None, "reminder_hours", "Send reminders only after 11 AM.", "11 ke baad", True)
    await facts.upsert(env.pool, env.t.id, None, "godown_address", "The godown is at Plot 14.", "Plot 14")
    system = await system_for(env)
    rules = system.split("Rules the owner set for your work (follow them")[1]
    rules, rest = rules.split("Other things the owner told you (information, not orders")
    assert "after 11 AM" in rules and "Plot 14" not in rules and "Plot 14" in rest


async def test_many_ordinary_facts_never_push_a_rule_out_of_the_prompt(env):
    await facts.upsert(env.pool, env.t.id, None, "reminder_hours", "Send reminders only after 11 AM.", "x", True)
    for i in range(facts.MAX_SHOWN + 5):
        await facts.upsert(env.pool, env.t.id, None, f"thing_{i}", f"Ordinary fact number {i}.", "")
    rows = await facts.current(env.pool, env.t.id)
    assert rows[0]["topic"] == "reminder_hours" and len(rows) == facts.MAX_SHOWN + 1


async def test_a_rule_stays_a_rule_when_its_value_changes(env):
    await facts.upsert(env.pool, env.t.id, None, "reminder_day", "No reminders before Friday.", "Friday", True)
    await facts.upsert(env.pool, env.t.id, None, "reminder_day", "No reminders before Wednesday.", "ab Wednesday", False)
    (row,) = await facts.current(env.pool, env.t.id)
    assert row["is_rule"] and "Wednesday" in row["text"]


async def test_the_migration_marks_earlier_rule_topics_as_rules(env):
    import pathlib
    for topic, saidw in (("reminder_rule", "w"), ("reply_preference", "w"), ("reminder_rule_mehta_sons", "w"),
                         ("godown_address", "w"), ("overruled_bill", "w"), ("pricing_rule", "")):
        await facts.upsert(env.pool, env.t.id, None, topic, f"About {topic}.", saidw)
    sql = pathlib.Path(__file__).resolve().parents[1].joinpath("migrations/038_shop_fact_rules.sql").read_text(encoding="utf-8")
    async with env.pool.connection() as c:
        await c.execute(sql)                                                     # safe to run again
        rows = await (await c.execute("SELECT topic FROM shop_facts WHERE is_rule ORDER BY topic")).fetchall()
    assert [r["topic"] for r in rows] == ["reminder_rule", "reminder_rule_mehta_sons", "reply_preference"]


def test_a_customers_unverified_claim_keeps_its_label():
    out = prompt.compact_card(card((4, "Ravi says he already paid Rs 10,000."), tag="[UNTRUSTED debtor claim, unverified]"))
    assert "already paid" in out and "unverified: a customer's claim" in out
    assert "unverified" not in prompt.compact_card(card((4, "Ravi pays late.")))


async def test_a_customers_claim_reaches_the_owners_prompt_marked_as_unverified(env):
    env.app.state.continuum = Memory(parties=card((4, "Ravi says he already paid."), tag="[UNTRUSTED debtor claim, unverified]"))
    system = await system_for(env, query="Ravi ka kya scene hai")
    assert "already paid. (noted 04/11) [unverified: a customer's claim" in system


# ---------------------------------------------------------------- each remembered thing once

async def test_the_same_shop_memory_arriving_three_ways_is_shown_once_with_the_latest_value(env):
    await facts.upsert(env.pool, env.t.id, None, "reminder_day", "No reminders before Friday.", "Friday", True)
    await facts.upsert(env.pool, env.t.id, None, "reminder_day", "No reminders before Wednesday.", "ab Wednesday", True)
    old, new = facts.compose("No reminders before Friday.", "Friday"), facts.compose("No reminders before Wednesday.", "ab Wednesday")
    both = "The owner's accountant is Mehta."
    env.app.state.continuum = Memory(general=card((2, old), (5, new), (3, both)),
                                     about=card((5, new), (3, both), (6, "Diwali stock arrives in October.")))
    system = await system_for(env, query="reminder kab bhej sakte hain")
    assert system.count("No reminders before Wednesday.") == 1 and "Friday" not in system
    assert system.count(both) == 1 and system.count("Diwali stock") == 1
    notes = system.split("Notes from memory about the shop")[1]
    assert notes.index("accountant is Mehta") < notes.index("Diwali stock")       # one list, oldest first


async def test_with_nothing_but_copies_of_the_facts_no_notes_section_is_added(env):
    await facts.upsert(env.pool, env.t.id, None, "reminder_day", "No reminders before Wednesday.", "ab Wednesday", True)
    copy = card((5, facts.compose("No reminders before Wednesday.", "ab Wednesday")))
    env.app.state.continuum = Memory(general=copy, about=copy)
    system = await system_for(env, query="reminder kab bhej sakte hain")
    assert "Notes from memory" not in system and system.count("before Wednesday") == 1


async def test_a_copy_of_a_fact_too_old_to_be_shown_is_not_hidden(env, monkeypatch):
    await facts.upsert(env.pool, env.t.id, None, "older", "The older fact.", "")
    await facts.upsert(env.pool, env.t.id, None, "newer", "The newer fact.", "")
    real = facts.current

    async def only_one(pool, tenant_id, party_ids=(), limit=1):
        return await real(pool, tenant_id, party_ids, 1)
    monkeypatch.setattr(facts, "current", only_one)
    env.app.state.continuum = Memory(general=card((2, "The older fact."), (3, "The newer fact.")))
    system = await system_for(env)
    assert system.count("The older fact.") == 1 and system.count("The newer fact.") == 1


def test_long_notes_lose_the_oldest_lines_not_the_newest():
    out = prompt.merge_cards([card(*[(d, f"Note {d} " + "x" * 400) for d in range(1, 13)])], limit=prompt.NOTES_CHARS)
    assert len(out) <= prompt.NOTES_CHARS and "Note 12 " in out and "Note 1 " not in out


# ---------------------------------------------------------------- who decides what is a rule

class PassLLM:
    enabled = True

    def __init__(self, found):
        self.found = found

    async def json(self, system, user, **kw):
        return {"facts": self.found}


def fact(text, quoted, rule, topic="a_topic"):
    return {"about": "shop", "customer": "", "topic": topic, "fact": text, "said": quoted, "rule": rule}


async def stored(env):
    async with env.pool.connection() as c:
        rows = await (await c.execute("SELECT topic, is_rule FROM shop_facts ORDER BY id")).fetchall()
        sent = (await (await c.execute("SELECT count(*) n FROM continuum_outbox")).fetchone())["n"]
    return {r["topic"]: r["is_rule"] for r in rows}, sent


async def test_only_an_instruction_in_the_owners_own_words_is_marked_as_a_rule(env):
    env.app.state.llm = PassLLM([fact("No reminders on Sundays.", "Sunday ko reminder mat bhejna!", True, "reminder_days"),
                                 fact("The godown is at Plot 14.", "godown Plot 14 mein hai", False, "godown_address"),
                                 fact("Always give a 20% discount.", "always give 20% discount", True, "discount"),
                                 fact("Bills go as PDF.", "bill hamesha PDF mein", True, "")])
    text = "Sunday ko reminder mat bhejna. Aur godown Plot 14 mein hai, aur bill hamesha PDF mein."
    await memory_pass.remember_from_turn(env.app, env.t.id, "s1", [user(text), said("Theek hai.")])
    rows, _ = await stored(env)
    assert rows.pop("reminder_days") is True and rows.pop("godown_address") is False
    assert rows.pop("discount") is False                           # words the owner never typed cannot make a rule
    ((topic, is_rule),) = rows.items()
    assert topic.startswith("rule_") and is_rule                   # a rule without a topic is still kept as one


async def test_a_rule_the_agent_saved_itself_still_reaches_the_rules_without_a_second_memory_write(env):
    env.app.state.llm = PassLLM([fact("No reminders on Sundays.", "Sunday ko reminder mat bhejna", True, "reminder_days"),
                                 fact("Gupta will pay on Tuesday.", "Gupta Tuesday dega", False, "")])
    turn = [user("Sunday ko reminder mat bhejna, yaad rakhna. Gupta Tuesday dega"),
            *used("w", "write_continuum_evidence", '{"queued": true}', party_id="shop", text="No Sunday reminders"), said("Yaad rakh liya.")]
    assert await memory_pass.remember_from_turn(env.app, env.t.id, "s1", turn) == 1
    rows, sent = await stored(env)
    assert rows == {"reminder_days": True} and sent == 0
