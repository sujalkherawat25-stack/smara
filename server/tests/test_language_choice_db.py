"""A language picked by name is saved by the server and kept. All names and numbers here are made up."""
import pytest
from test_agent_db import ScriptLLM, env, say  # noqa: F401
from test_agent_db import owner as typed

from src import agent_loop, chatlog, prefs, tenants
from src.ingest.dispatch import dispatch

INTRO = "नमस्ते! आपको किस भाषा में बात करनी है? लिखकर भेजें: *हिंदी*, *Hinglish* या *English*"


def last_text(env):
    return [m["text"]["body"] for m in env.app.state.wa.sent if m.get("type") == "text"][-1]


async def smara_said(env, text):
    await chatlog.record(env.pool, env.t.id, "whatsapp", "smara", text, kind="owner_note")


async def language(env):
    return (await prefs.get(env.pool, env.t.id))["language"]


@pytest.mark.parametrize("said,lang", [("हिंदी", "hindi"), ("*Hinglish*", "hinglish"), (" English. ", "english"),
                                       ("hindi", "hindi"), ("हिन्दी।", "hindi")])
async def test_a_bare_language_name_is_saved_without_the_model(env, said, lang):
    env.app.state.llm = ScriptLLM()
    await smara_said(env, INTRO)
    await dispatch(env.app, typed("l1", said))
    assert await language(env) == lang
    assert last_text(env) == prefs.CHOSEN[lang] and not env.app.state.llm.seen


async def test_the_first_message_ever_can_be_the_choice(env):
    env.app.state.llm = ScriptLLM()
    await dispatch(env.app, typed("f1", "Hindi"))
    assert await language(env) == "hindi" and not env.app.state.llm.seen


async def test_a_language_name_answering_a_question_goes_to_the_assistant(env):
    env.app.state.llm = ScriptLLM(say("translated"))
    await smara_said(env, "Which language should I translate it into?")
    await dispatch(env.app, typed("q1", "Hindi"))
    assert await language(env) == "auto" and len(env.app.state.llm.seen) == 1


async def test_a_sentence_naming_a_language_goes_to_the_assistant(env):
    env.app.state.llm = ScriptLLM(say("ok"))
    await smara_said(env, INTRO)
    await dispatch(env.app, typed("s1", "translate this into English"))
    assert await language(env) == "auto" and len(env.app.state.llm.seen) == 1


async def test_the_chosen_language_is_in_the_instructions_whatever_they_type_next(env):
    env.app.state.llm = ScriptLLM(say("Sure."))
    await smara_said(env, INTRO)
    await dispatch(env.app, typed("c1", "English"))
    await dispatch(env.app, typed("c2", "meri dukaan hai, kya madad milegi"))
    seen = str(env.app.state.llm.seen[-1])
    assert "Write EVERY answer to the owner in simple Indian English" in seen
    assert "for someone else goes in the language they ask for" in seen


async def test_progress_notes_follow_the_chosen_language(env):
    tenant = await tenants.by_id(env.pool, env.t.id)
    assert await agent_loop._progress_notes(env.app, tenant) == agent_loop.PROGRESS_NOTES
    await prefs.put(env.pool, env.t.id, language="hindi")
    assert await agent_loop._progress_notes(env.app, tenant) == agent_loop.PROGRESS_NOTES_IN["hindi"]


HINGLISH = ("Bahut simple hai, aap mujhe batayenge, main sambhal lungi. Customer ka naam aur amount bata dijiye, "
            "main ledger mein likh dungi.")


@pytest.mark.parametrize("lang,reply,rewrite", [
    ("english", HINGLISH, True),
    ("english", 'Just write to me like "Sharma ko 12000 ka maal diya, 15 din mein dega" and I will record it.', False),
    ("english", "You have two options: tell me each entry here, or send me your Excel file of dues.", False),
    ("hindi", "You have two options: tell me each entry here, or send me your Excel file and I will read it for you.", True),
    ("hindi", "आपकी Excel फ़ाइल तैयार है। इसमें GST और TDS के कॉलम हैं।", False),
    ("hinglish", HINGLISH, False),
    ("auto", HINGLISH, False),
])
def test_only_a_reply_clearly_in_the_other_language_is_written_again(lang, reply, rewrite):
    assert bool(prefs.wrong_language(lang, reply)) is rewrite


async def test_a_hinglish_reply_is_rewritten_before_an_english_user_sees_it(env):
    env.app.state.llm = ScriptLLM(say(HINGLISH), say("It is simple. Tell me the customer's name and the amount."))
    await prefs.put(env.pool, env.t.id, language="english")
    await dispatch(env.app, typed("w1", "udhari ka hisaab kaise rakhun?"))
    assert last_text(env) == "It is simple. Tell me the customer's name and the amount."
    assert not any("sambhal lungi" in str(m) for m in env.app.state.wa.sent)
