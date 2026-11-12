"""A changed rule replaces the old one; facts keep their direction and the owner's words; older related messages are found."""
import pytest
from test_agent_db import env, one  # noqa: F401

from src import chatlog, facts, tenants
from src.agent import memory_pass, prompt
from src.agent.prompt import build_system


@pytest.fixture(autouse=True)
def _fresh_memory_state():
    prompt._memory_down_until = 0.0
    memory_pass.STATS.update(runs=0, failed=0, empty=0, written=0)
    yield
    prompt._memory_down_until = 0.0


class PassLLM:
    """Stands in for the model that extracts facts: returns what it is told to, and keeps what it was asked."""
    enabled = True

    def __init__(self, *answers):
        self.answers, self.asked = list(answers), []

    async def json(self, system, user, **kw):
        self.asked.append((system, user))
        return {"facts": self.answers.pop(0)}


def turn(owner_text, reply="Theek hai."):
    return [{"role": "system", "content": "x"}, {"role": "user", "content": owner_text}, {"role": "assistant", "content": reply}]


async def facts_rows(env):
    async with env.pool.connection() as c:
        return await (await c.execute("SELECT slot, topic, text, said, superseded FROM shop_facts ORDER BY id")).fetchall()


async def outbox(env):
    async with env.pool.connection() as c:
        return await (await c.execute("SELECT user_id, text FROM continuum_outbox ORDER BY id")).fetchall()


# ---------------------------------------------------------------- one current row per topic

async def test_a_new_value_for_a_topic_replaces_the_old_one_and_remembers_the_old_wording(env):
    assert await facts.upsert(env.pool, env.t.id, None, "godown_address", "The godown is at Plot 14.", "Godown Plot 14 mein hai") == "new"
    assert await facts.upsert(env.pool, env.t.id, None, "godown_address", "The godown is at Plot 14.", "") == "same"
    assert await facts.upsert(env.pool, env.t.id, None, "godown_address", "The godown moved to Plot 22.", "ab Plot 22 mein") == "updated"
    (row,) = await facts_rows(env)
    assert row["text"] == "The godown moved to Plot 22." and row["said"] == "ab Plot 22 mein"
    assert row["superseded"] == ['The godown is at Plot 14. (owner\'s words: "Godown Plot 14 mein hai")']
    assert await facts.superseded_texts(env.pool, env.t.id) == frozenset(row["superseded"])
    assert await facts.superseded_texts(env.pool, env.other.id) == frozenset()                # another shop sees none of it


async def test_topics_are_per_customer_and_current_lists_only_the_shop_and_the_customers_named(env):
    await facts.upsert(env.pool, env.t.id, None, "reminder_hours", "Send reminders only after 11 AM.", "")
    await facts.upsert(env.pool, env.t.id, env.pid, "reminder_rule", "Ravi: no reminders before Friday.", "")
    await facts.upsert(env.pool, env.t.id, env.pid, "reminder_rule", "Ravi: no reminders before Wednesday.", "")
    await facts.upsert(env.pool, env.other.id, None, "reminder_hours", "Other shop's secret rule.", "")
    assert len([r for r in await facts_rows(env)]) == 3                                       # Ravi's rule is one row, not two
    shown = [r["text"] for r in await facts.current(env.pool, env.t.id, [])]
    assert shown == ["Send reminders only after 11 AM."]                                       # Ravi's only when Ravi is named
    shown = [r["text"] for r in await facts.current(env.pool, env.t.id, [env.pid])]
    assert set(shown) == {"Send reminders only after 11 AM.", "Ravi: no reminders before Wednesday."}


# ---------------------------------------------------------------- the memory pass

async def test_a_changed_rule_replaces_the_old_one_and_both_writes_carry_the_owners_words(env):
    llm = env.app.state.llm = PassLLM(
        [{"about": "customer", "customer": "Ravi", "topic": "Reminder Rule", "fact": "No reminders to Ravi before Friday.",
          "said": "Ravi ko Friday se pehle reminder mat bhejna"}],
        [{"about": "customer", "customer": "Ravi", "topic": "reminder_rule", "fact": "Ravi's reminder rule changed to: none before Wednesday.",
          "said": "Ravi ke liye ab Wednesday se pehle mat bhejna"}])
    assert await memory_pass.remember_from_turn(env.app, env.t.id, "s1", turn("Ravi ko Friday se pehle reminder mat bhejna")) == 1
    assert await memory_pass.remember_from_turn(env.app, env.t.id, "s2", turn("Ravi ke liye ab Wednesday se pehle mat bhejna")) == 1
    (row,) = await facts_rows(env)
    assert row["slot"] == f"{env.pid}:reminder_rule" and "Wednesday" in row["text"] and "Friday" not in row["text"]
    assert len(row["superseded"]) == 1 and "Friday" in row["superseded"][0]
    sent = await outbox(env)
    assert [r["user_id"] for r in sent] == [f"{env.t.id}_{env.pid}"] * 2
    assert '(owner\'s words: "Ravi ko Friday se pehle reminder mat bhejna")' in sent[0]["text"]
    assert "Facts already on file: none." in llm.asked[0][1]
    assert "reminder_rule | Ravi |" in llm.asked[1][1]                                           # the second call is shown the first fact
    assert "WHO PAYS WHOM" in llm.asked[0][0] and "ko ... dene hain" in llm.asked[0][0]         # direction is part of the instructions


async def test_facts_without_a_topic_sit_next_to_each_other_and_an_unknown_customer_does_not_collide_with_the_shop(env):
    env.app.state.llm = PassLLM(
        [{"about": "customer", "customer": "Gupta Traders", "topic": "", "fact": "Gupta will pay 45,000 on Tuesday.", "said": "Gupta Tuesday dega"},
         {"about": "customer", "customer": "Gupta Traders", "topic": "", "fact": "Gupta disputes 3 boxes.", "said": ""},
         {"about": "customer", "customer": "Mehta Sons", "topic": "reminder_rule", "fact": "No reminders to Mehta Sons on Mondays.", "said": ""},
         {"about": "shop", "customer": "", "topic": "reminder_rule", "fact": "Reminders only after 11 AM.", "said": ""}])
    assert await memory_pass.remember_from_turn(env.app, env.t.id, "s1", turn("kai baatein ek saath bata raha hoon")) == 4
    topics = sorted(r["topic"] for r in await facts_rows(env))
    assert topics == ["reminder_rule", "reminder_rule_mehta_sons"]                              # Mehta's rule did not replace the shop's
    assert len(await outbox(env)) == 4                                                          # both Gupta facts are kept
    assert any("About Mehta Sons:" in r["text"] for r in await outbox(env))


# ---------------------------------------------------------------- what the agent sees

def card_line(text, minute):
    return (f"[UNVERSIONED EVIDENCE] [TRUSTED] {text} (source=m_{minute}, turn=turn:1, "
            f"valid_at=2026-11-12T08:{minute:02d}:00.000000+00:00)")


async def test_the_agent_sees_the_current_value_and_the_replaced_one_is_hidden_even_when_continuum_still_returns_it(env):
    env.app.state.llm = PassLLM(
        [{"about": "customer", "customer": "Ravi", "topic": "reminder_rule", "fact": "No reminders to Ravi before Friday.", "said": "Friday se pehle nahi"}],
        [{"about": "customer", "customer": "Ravi", "topic": "reminder_rule", "fact": "Ravi's rule changed to: none before Wednesday.", "said": "ab Wednesday"}])
    await memory_pass.remember_from_turn(env.app, env.t.id, "s1", turn("Ravi ko Friday se pehle nahi bhejna"))
    await memory_pass.remember_from_turn(env.app, env.t.id, "s2", turn("Ravi ke liye ab Wednesday tak ruko"))
    old = facts.compose("No reminders to Ravi before Friday.", "Friday se pehle nahi")
    new = facts.compose("Ravi's rule changed to: none before Wednesday.", "ab Wednesday")

    class Mem:                                                  # Continuum, which returns both and cannot tell which is newer
        async def resolve(self, **kw):
            return {"resolution": {"state_card": "RETRIEVED EVIDENCE\n" + "\n".join([card_line(new, 15), card_line(old, 2)])}}
    env.app.state.continuum = Mem()
    tenant = await tenants.by_id(env.pool, env.t.id)
    system = await build_system(env.app, tenant, "TRUSTED", None, query="Ravi ko reminder kab se bhej sakte hain?")
    assert "What the owner has told you" in system and "Ravi: Ravi's rule changed to: none before Wednesday. (told" in system
    assert "before Friday" not in system and "Friday se pehle" not in system        # replaced in the facts, the standing list and the lookup
    assert "Wednesday" in system.split("Memory about this message")[1]             # Continuum's current line still comes through


async def test_without_a_memory_service_the_current_facts_are_still_shown(env):
    await facts.upsert(env.pool, env.t.id, None, "reminder_hours", "Send reminders only after 11 AM.", "")

    class Down:
        async def resolve(self, **kw):
            raise ConnectionError("down")
    env.app.state.continuum = Down()
    tenant = await tenants.by_id(env.pool, env.t.id)
    system = await build_system(env.app, tenant, "TRUSTED", None, query="Reminders kab bhejne hain?")
    assert "Send reminders only after 11 AM." in system and prompt.MEMORY_DOWN_NOTE in system


async def test_customer_sessions_never_see_the_owners_facts_or_old_chats(env):
    await facts.upsert(env.pool, env.t.id, None, "reminder_hours", "Secret shop rule.", "")
    tenant = await tenants.by_id(env.pool, env.t.id)
    system = await build_system(env.app, tenant, "UNTRUSTED", None, query="rule kya hai")
    assert "Secret shop rule" not in system and "What the owner has told you" not in system


# ---------------------------------------------------------------- older messages related to the question

async def say_at(env, days, role, text):
    async with env.pool.connection() as c:
        await c.execute("""INSERT INTO chat_messages (tenant_id, channel, role, content, created_at)
                           VALUES (%s, 'telegram', %s, %s, now() - %s * interval '1 day')""", (env.t.id, role, text, days))


async def test_older_messages_about_the_question_are_found_for_the_agent_and_recent_ones_are_not_repeated(env):
    await say_at(env, 40, "owner", "Tally ka kaam Dinesh dekhta hai, uska naam yaad rakhna")
    await say_at(env, 41, "owner", "Aaj mausam accha hai")
    for i in range(chatlog.RECENT_LINES):                          # fill the recent window so the old message is outside it
        await say_at(env, 0.5 - i / 1000, "owner" if i % 2 else "smara", f"chatter number {i} about nothing")
    found = await chatlog.related(env.pool, env.t.id, "Tally kaun dekhta hai?")
    assert "Dinesh dekhta hai" in found and "mausam" not in found
    assert "Dinesh" in await chatlog.related(env.pool, env.t.id, "Tally")                  # one word to look for: one match is enough
    assert await chatlog.related(env.pool, env.t.id, "chatter number nothing") == ""      # recent lines are not repeated
    system = await build_system(env.app, await tenants.by_id(env.pool, env.t.id), "TRUSTED", None, query="Tally kaun dekhta hai?")
    assert "Older messages that may be about" in system and "Dinesh dekhta hai" in system


async def test_one_matching_word_is_not_enough_when_the_question_has_several(env):
    await say_at(env, 40, "owner", "Tally mein naya version aaya hai")
    for i in range(chatlog.RECENT_LINES):
        await say_at(env, 0.5 - i / 1000, "owner", f"filler {i}")
    assert await chatlog.related(env.pool, env.t.id, "Tally kaun dekhta hai?") == ""      # only "tally" matches
    system = await build_system(env.app, await tenants.by_id(env.pool, env.t.id), "TRUSTED", None, query="Tally kaun dekhta hai?")
    assert "Older messages that may be about" not in system


# ---------------------------------------------------------------- money statements are kept, and losing facts is visible

def test_the_instructions_say_that_money_owed_either_way_is_worth_keeping_and_that_the_list_on_file_never_excuses_skipping():
    """Found by replaying a scenario through the real model: shown a list of facts already on file, it dropped statements such as
    'Khan Brothers owes me 80,000' and 'I owe Singh 32,500' (3 of 17 in one run, 1 of 17 in the next)."""
    text = memory_pass.SYSTEM
    assert "Money counts" in text and "owe someone" in text and "someone owes them" in text and "in which direction" in text
    assert "even when the assistant's reply repeats it" in text
    assert "ONLY so you can reuse a topic" in text and "never skip a new fact because something similar is on file" in text
    assert "amounts or dates already in the ledger" not in text                 # the clause that read as 'skip money'
    assert "invoices and amounts that come from a sheet or the ledger" in text


async def test_the_pass_counts_what_it_did_and_a_failed_model_call_is_logged_not_silent(env, caplog):
    class Broken:
        enabled = True

        async def json(self, system, user, **kw):
            raise TimeoutError("model timed out")
    env.app.state.llm = Broken()
    with caplog.at_level("WARNING"):
        assert await memory_pass.remember_from_turn(env.app, env.t.id, "s1", turn("Mujhe Singh Traders ko 32,500 dene hain, 5 tareekh tak")) == 0
    assert "lost this turn's facts" in caplog.text and "TimeoutError" in caplog.text
    assert memory_pass.stats() == {"runs": 1, "failed": 1, "empty": 0, "written": 0}

    env.app.state.llm = PassLLM([], [{"about": "customer", "customer": "Ravi", "topic": "", "fact": "Ravi will pay 5,000 on Monday.", "said": "Ravi Monday dega"}])
    assert await memory_pass.remember_from_turn(env.app, env.t.id, "s2", turn("aaj mausam bahut accha hai yaar")) == 0
    assert await memory_pass.remember_from_turn(env.app, env.t.id, "s3", turn("Ravi Monday ko 5,000 dega bola hai")) == 1
    assert memory_pass.stats() == {"runs": 3, "failed": 1, "empty": 1, "written": 1}
    assert await memory_pass.remember_from_turn(env.app, env.t.id, "s4", turn("hi")) == 0               # too short to look at: not a run
    assert memory_pass.stats()["runs"] == 3
