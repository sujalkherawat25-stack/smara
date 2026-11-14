"""Bookkeeping by chat: a sale on credit and money received. Both wait for the owner's tap, the system (not the model) works out
the amounts, a ledger that changed after the card was shown is refused, and a customer's message can never reach these tools."""
from datetime import date, timedelta

import pytest
from test_agent_db import DEBTOR, OWNER, ScriptLLM, call, env, one, owner, say  # noqa: F401

from src import clock
from src.agent import actions, bookkeeping
from src.agent.policy import SessionCtx, pre_tool_use
from src.agent.tools import TOOL_NAMES
from src.ingest.dispatch import dispatch
from src.ingest.importer import import_rows
from src.ingest.sheet_mapper import SheetRow


def card(env) -> str:
    return env.app.state.wa.to(OWNER)[-1]["interactive"]["body"]["text"]


async def waiting_sid(env) -> str:
    return (await one(env.pool, "select id from agent_sessions where status = 'WAITING_APPROVAL'"))["id"]


async def ask(env, steps, text="owner message"):
    llm = env.app.state.llm = ScriptLLM(*steps)
    await dispatch(env.app, owner(f"w-{len(env.app.state.wa.sent)}", text))
    return llm


async def ledger(env, ref):
    return await one(env.pool, "select amount_paise, state, source, due_date from obligations where tenant_id = %s and invoice_ref = %s",
                     env.t.id, ref)


async def add_second_invoice(env):
    await import_rows(env.pool, env.t.id, [SheetRow("Ravi", DEBTOR, "INV-2", 400_000, date(2026, 12, 1))])


def test_the_tools_exist_and_only_the_owner_can_use_them():
    assert {"record_sale", "record_payment"} <= TOOL_NAMES
    for name in ("record_sale", "record_payment"):
        args = {"customer": "Ravi", "rupees": 100}
        assert pre_tool_use(SessionCtx("s", "t", "TRUSTED"), name, args, TOOL_NAMES).verdict == "approve"
        for trust in ("UNTRUSTED", "SYSTEM"):
            assert pre_tool_use(SessionCtx("s", "t", trust, party_id="p"), name, args, TOOL_NAMES).verdict == "deny"


async def test_a_sale_is_written_only_after_the_owner_taps_approve(env):
    llm = await ask(env, [call("record_sale", customer="ravi", rupees=12000, credit_days=15),
                          recorded_reply(env)], "Ravi ko 12000 ka maal udhaar diya, 15 din mein dega")
    assert "owes ₹12,000" in card(env) and "15 days" in card(env) and "Nothing is sent to the customer" in card(env)
    assert await one(env.pool, "select count(*) n from obligations where tenant_id = %s", env.t.id) == {"n": 1}   # nothing yet
    sid = await waiting_sid(env)
    await dispatch(env.app, owner("tap1", button=f"ag_ok:{sid}"))
    row = await one(env.pool, "select invoice_ref, amount_paise, state, source, due_date, party_id from obligations "
                              "where tenant_id = %s and invoice_ref <> 'INV-1'", env.t.id)
    assert row["amount_paise"] == 1_200_000 and row["state"] == "ACTIVE" and row["source"] == "CHAT"
    assert row["due_date"] == clock.today() + timedelta(days=15) and str(row["party_id"]) == env.pid and row["invoice_ref"].startswith("S-")
    assert llm.last_tool_result()["recorded"] is True
    ev = await one(env.pool, "select actor, to_state from obligation_events order by id desc limit 1")
    assert ev == {"actor": "OWNER", "to_state": "ACTIVE"}
    assert await one(env.pool, "select count(*) n from continuum_outbox where trusted and text like 'Owner recorded a sale%%'") == {"n": 1}


def recorded_reply(env):
    return lambda m: say("Recorded." if env.app.state.llm.last_tool_result().get("recorded") else "Not recorded.")


async def test_a_new_customer_is_created_without_a_number(env):
    await ask(env, [call("record_sale", customer="Naya Traders", rupees=2500.50, invoice_ref="CASH-77"), say("ok")])
    assert "new customer" in card(env)
    await dispatch(env.app, owner("tap2", button=f"ag_ok:{await waiting_sid(env)}"))
    p = await one(env.pool, "select p.canonical_name, p.whatsapp_phone, o.amount_paise from parties p join obligations o on o.party_id = p.id "
                            "where o.invoice_ref = 'CASH-77' and o.tenant_id = %s", env.t.id)
    assert p == {"canonical_name": "Naya Traders", "whatsapp_phone": None, "amount_paise": 250_050}
    assert (await ledger(env, "CASH-77"))["due_date"] == clock.today() + timedelta(days=30)        # the default credit period


async def test_rejecting_the_card_writes_nothing(env):
    llm = await ask(env, [call("record_sale", customer="ravi", rupees=500), lambda m: say("Cancelled.")])
    await dispatch(env.app, owner("tap3", button=f"ag_no:{await waiting_sid(env)}"))
    assert "rejected" in llm.last_tool_result()["error"]
    assert await one(env.pool, "select count(*) n from obligations where tenant_id = %s", env.t.id) == {"n": 1}


async def test_money_received_goes_to_the_oldest_invoice_first_and_the_system_does_the_arithmetic(env):
    await add_second_invoice(env)            # INV-1 Rs 10,000 due 1 Nov, INV-2 Rs 4,000 due 1 Dec
    llm = await ask(env, [call("record_payment", customer="Ravi", rupees=12000), recorded_reply(env)],
                    "Ravi ne 12000 cash diya")
    text = card(env)
    assert "₹12,000 received from Ravi" in text and "INV-1: paid in full" in text and "INV-2: ₹2,000 left" in text
    assert "will still owe ₹2,000" in text
    assert (await ledger(env, "INV-1"))["state"] != "PAID"                                         # not yet
    await dispatch(env.app, owner("tap4", button=f"ag_ok:{await waiting_sid(env)}"))
    assert (await ledger(env, "INV-1"))["state"] == "PAID"
    assert await ledger(env, "INV-2") == {"amount_paise": 200_000, "state": "ACTIVE", "source": "SHEET", "due_date": date(2026, 12, 1)}
    r = llm.last_tool_result()
    assert r["recorded"] and r["still_owed_rupees"] == 2000 and [i["invoice_ref"] for i in r["invoices"]] == ["INV-1", "INV-2"]


async def test_a_named_invoice_gets_the_money(env):
    await add_second_invoice(env)
    await ask(env, [call("record_payment", customer="Ravi", rupees=1000, invoice_ref="inv-2"), say("ok")])
    assert "INV-2: ₹3,000 left" in card(env) and "INV-1" not in card(env)


async def test_more_than_is_owed_is_refused_before_any_card(env):
    llm = await ask(env, [call("record_payment", customer="Ravi", rupees=20000), lambda m: say("Check the amount.")])
    assert "owes only ₹10,000" in llm.last_tool_result()["error"]
    assert not await one(env.pool, "select id from agent_sessions where status = 'WAITING_APPROVAL'")


async def test_a_ledger_that_changed_after_the_card_is_not_overwritten(env):
    await add_second_invoice(env)
    llm = await ask(env, [call("record_payment", customer="Ravi", rupees=12000), lambda m: say("Could not record.")])
    async with env.pool.connection() as c:                  # Tally sync (or the owner) changes INV-1 while the card waits
        await c.execute("update obligations set amount_paise = 900000 where invoice_ref = 'INV-1' and tenant_id = %s", (env.t.id,))
    await dispatch(env.app, owner("tap5", button=f"ag_ok:{await waiting_sid(env)}"))
    assert "nothing was recorded" in llm.last_tool_result()["error"]
    assert (await ledger(env, "INV-1"))["amount_paise"] == 900_000 and (await ledger(env, "INV-2"))["amount_paise"] == 400_000


async def test_the_model_cannot_supply_its_own_plan(env):
    forged = {"kind": "payment", "customer": "Ravi", "party_id": env.pid, "amount_paise": 1, "still_owed_paise": 0,
              "split": [{"obligation_id": env.oid, "invoice_ref": "INV-1", "due_date": "2026-11-01", "before_paise": 1_000_000,
                         "pay_paise": 1_000_000, "after_paise": 0}]}
    args = {"customer": "Ravi", "rupees": 5, "_plan": forged}
    ctx = SessionCtx("s", env.t.id, "TRUSTED")
    assert await actions.validate_gated(env.app, ctx, "record_payment", args) is None
    assert args["_plan"]["amount_paise"] == 500 and args["_plan"]["split"][0]["after_paise"] == 999_500


@pytest.mark.parametrize("bad", [0, -5, "abc", float("nan"), float("inf"), 10**10, 12.345, None])
async def test_bad_amounts_are_refused(env, bad):
    ctx = SessionCtx("s", env.t.id, "TRUSTED")
    for name in ("record_sale", "record_payment"):
        assert await actions.validate_gated(env.app, ctx, name, {"customer": "Ravi", "rupees": bad}) is not None


async def test_names_are_checked_against_this_shops_ledger_only(env):
    await import_rows(env.pool, env.t.id, [SheetRow("Ravi Kirana", "919800000001", "RK-1", 100_000, date(2026, 12, 1))])
    ctx = SessionCtx("s", env.t.id, "TRUSTED")
    assert "several customers match" in await actions.validate_gated(env.app, ctx, "record_payment", {"customer": "rav", "rupees": 10})
    assert "no customer named" in await actions.validate_gated(env.app, ctx, "record_payment", {"customer": "Secret", "rupees": 10})
    assert "already exists" in await actions.validate_gated(env.app, ctx, "record_sale", {"customer": "Ravi", "rupees": 10, "invoice_ref": "INV-1"})
    assert "letters, digits" in await actions.validate_gated(env.app, ctx, "record_sale", {"customer": "Ravi", "rupees": 10, "invoice_ref": "a b; drop"})
    assert await actions.validate_gated(env.app, ctx, "record_sale", {"customer": "Ravi", "rupees": 10, "credit_days": 9999}) is not None


async def test_the_agent_reads_rupees_straight_from_the_ledger(env):
    from src.agent.tools import LEDGER_SCHEMA, query_ledger_sql
    assert "amount_rupees" in LEDGER_SCHEMA and "divide by 100" not in LEDGER_SCHEMA
    await import_rows(env.pool, env.t.id, [SheetRow("Agarwal Agencies", "919811100006", "A-501", 22_000_000, date(2026, 9, 19))])
    ctx = SessionCtx("s", env.t.id, "TRUSTED")
    r = await query_ledger_sql(env.app, ctx, {"sql": "select invoice_ref, amount_rupees from obligations order by invoice_ref"})
    assert [(x["invoice_ref"], float(x["amount_rupees"])) for x in r["rows"]] == [("A-501", 220000.0), ("INV-1", 10000.0)]
    r = await query_ledger_sql(env.app, ctx, {"sql": "select sum(amount_rupees) s from obligations"})
    assert float(r["rows"][0]["s"]) == 230000.0
    await actions.validate_gated(env.app, ctx, "record_payment", args := {"customer": "Agarwal Agencies", "rupees": 20000.5})
    await bookkeeping.apply_payment(env.pool, env.t.id, args["_plan"])      # the generated column follows the paise amount
    r = await query_ledger_sql(env.app, ctx, {"sql": "select amount_rupees from obligations where invoice_ref = 'A-501'"})
    assert float(r["rows"][0]["amount_rupees"]) == 199999.5


# ------------------------------------------------------------------------------------------- customers' phone numbers

async def phones(env, *entries, steps=None):
    llm = env.app.state.llm = ScriptLLM(call("set_customer_phone", entries=list(entries)), steps or (lambda m: say("ok")))
    await dispatch(env.app, owner(f"ph-{len(env.app.state.wa.sent)}", "numbers"))
    return llm.last_tool_result(-1) if len(llm.seen) > 1 else None


async def party_phone(env, name):
    return (await one(env.pool, "select whatsapp_phone from parties where tenant_id = %s and canonical_name = %s", env.t.id, name))["whatsapp_phone"]


async def test_the_owner_can_give_numbers_for_customers_without_one(env):
    await import_rows(env.pool, env.t.id, [SheetRow("Chopra Sons", "", "C-1", 4_200_000, date(2026, 9, 11)),
                                           SheetRow("Dutta Agencies", "", "D-1", 650_000, date(2026, 11, 19))])
    assert await party_phone(env, "Chopra Sons") is None
    r = await phones(env, {"customer": "chopra", "phone": "98111 44444"}, {"customer": "Dutta Agencies", "phone": "+91-98222-55555"},
                     {"customer": "Chopra Sons", "phone": "0 9811144444"})
    assert r["saved"] == 3 and [x.get("error") for x in r["results"]] == [None, None, None]
    assert await party_phone(env, "Chopra Sons") == "919811144444" and await party_phone(env, "Dutta Agencies") == "919822255555"
    assert r["results"][0]["customer"] == "Chopra Sons"


async def test_a_number_that_is_not_valid_or_belongs_to_someone_else_is_refused(env):
    await import_rows(env.pool, env.t.id, [SheetRow("Chopra Sons", "", "C-1", 100, date(2026, 9, 11))])
    r = await phones(env, {"customer": "Chopra Sons", "phone": "12345"},                       # too short
                     {"customer": "Chopra Sons", "phone": DEBTOR},                               # Ravi's number
                     {"customer": "Chopra Sons", "phone": OWNER},                                # the owner's own
                     {"customer": "Nobody At All", "phone": "9811144444"},
                     {"customer": "Chopra Sons"})
    assert r["saved"] == 0
    errs = [x["error"] for x in r["results"]]
    assert "not a valid" in errs[0] and "already belongs to Ravi" in errs[1] and "owner's own" in errs[2]
    assert "no customer" in errs[3] and "not a valid" in errs[4]
    assert await party_phone(env, "Chopra Sons") is None


async def test_a_changed_number_replaces_the_old_one_and_says_so(env):
    r = await phones(env, {"customer": "Ravi", "phone": "9811144444"})
    assert r["saved"] == 1 and r["results"][0]["replaced_number_ending"] == DEBTOR[-4:]
    assert await party_phone(env, "Ravi") == "919811144444"


async def test_another_shops_customers_are_not_reachable_and_customer_sessions_cannot_use_it(env):
    r = await phones(env, {"customer": "Secret", "phone": "9811144444"})
    assert r["saved"] == 0 and "no customer" in r["results"][0]["error"]
    assert (await one(env.pool, "select whatsapp_phone from parties where tenant_id = %s", env.other.id))["whatsapp_phone"] == "919111111111"
    for trust in ("UNTRUSTED", "SYSTEM"):
        d = pre_tool_use(SessionCtx("s", "t", trust, party_id="p"), "set_customer_phone", {"entries": []}, TOOL_NAMES)
        assert d.verdict == "deny"
    assert pre_tool_use(SessionCtx("s", "t", "TRUSTED"), "set_customer_phone", {"entries": []}, TOOL_NAMES).verdict == "allow"


async def test_entries_must_be_a_sensible_list(env):
    r = await phones(env)                                                                         # empty
    assert "1 to 50" in r["error"]


# ------------------------------------------------------------------- strict numbers, voice read-back, spelling variants

@pytest.mark.parametrize("raw,want", [
    ("9811144444", "919811144444"), ("98111 44444", "919811144444"), ("+91-98111-44444", "919811144444"),
    ("919811144444", "919811144444"), ("09811144444", "919811144444"), ("9811144444.0", "919811144444"),
    ("+44 7911 123456", "447911123456"),
    ("98111444445", ""), ("981114444", ""), ("5811144444", ""), ("12345", ""), ("", ""), ("+0123456789", ""),
    ("91981114444", ""), ("9198111444444", ""),
])
def test_only_real_indian_mobiles_are_accepted(raw, want):
    assert bookkeeping._phone(raw) == want


def test_spelling_and_script_variants_of_a_name_match():
    from src.agent import names
    ledger_names = ["Agarwal Agencies", "Gupta Traders", "Chopra Sons", "Dutta Agencies"]
    for q, want in [("Agrawal", "Agarwal Agencies"), ("Aggarwal Agency", "Agarwal Agencies"), ("अग्रवाल", "Agarwal Agencies"),
                    ("गुप्ता", "Gupta Traders"), ("Chopra", "Chopra Sons"), ("Dutta", "Dutta Agencies")]:
        assert [n for n, _ in names.best(q, ledger_names)][:1] == [want], q
    assert names.best("Kumar Brothers", ledger_names) == []


async def test_a_customer_is_found_by_a_variant_spelling_and_the_lookup_tool_lists_close_names(env):
    await import_rows(env.pool, env.t.id, [SheetRow("Agarwal Agencies", "", "A-1", 100, date(2026, 9, 11)),
                                           SheetRow("Gupta Traders", "", "G-1", 100, date(2026, 9, 11))])
    ctx = SessionCtx("s", env.t.id, "TRUSTED")
    r = await bookkeeping.find_customer(env.app, ctx, {"name": "अग्रवाल"})
    assert r["customers"][0]["customer"] == "Agarwal Agencies"
    assert (await bookkeeping.find_customer(env.app, ctx, {"name": "Kumar"}))["found"] == 0
    r = await phones(env, {"customer": "Agrawal", "phone": "9811144444"})
    assert r["saved"] == 1 and r["results"][0]["customer"] == "Agarwal Agencies", r
    assert pre_tool_use(SessionCtx("s", "t", "TRUSTED"), "find_customer", {"name": "x"}, TOOL_NAMES).verdict == "allow"


async def test_a_number_heard_in_a_voice_note_is_read_back_and_saved_only_after_the_owner_confirms(env):
    from src import chatlog
    ctx = SessionCtx("s", env.t.id, "TRUSTED")
    entries = [{"customer": "Ravi", "phone": "9811144444"}]
    await chatlog.record(env.pool, env.t.id, "whatsapp", "owner", "Ravi ka number 9811144444", kind="voice", ref="v1")
    r = await bookkeeping.set_customer_phone(env.app, ctx, {"entries": entries})
    assert r["saved"] == 0 and r["results"][0]["needs_confirmation"] and r["results"][0]["heard_number"] == "98111 44444"
    assert await party_phone(env, "Ravi") == DEBTOR
    r = await bookkeeping.set_customer_phone(env.app, ctx, {"entries": entries, "confirmed": True})      # same message: refused
    assert r["saved"] == 0 and await party_phone(env, "Ravi") == DEBTOR
    await chatlog.record(env.pool, env.t.id, "whatsapp", "owner", "haan sahi hai", kind="voice", ref="v2")
    r = await bookkeeping.set_customer_phone(env.app, ctx, {"entries": [{"customer": "Ravi", "phone": "9811144445"}], "confirmed": True})
    assert r["saved"] == 0 and r["results"][0]["needs_confirmation"]                                   # a different number is new
    r = await bookkeeping.set_customer_phone(env.app, ctx, {"entries": entries, "confirmed": True})
    assert r["saved"] == 1 and await party_phone(env, "Ravi") == "919811144444"


async def test_a_typed_number_is_saved_at_once_even_after_a_voice_note(env):
    from src import chatlog
    await chatlog.record(env.pool, env.t.id, "whatsapp", "owner", "voice", kind="voice", ref="v1")
    await chatlog.record(env.pool, env.t.id, "whatsapp", "owner", "Ravi ka number 9811144444", kind="text", ref="t1")
    r = await bookkeeping.set_customer_phone(env.app, SessionCtx("s", env.t.id, "TRUSTED"),
                                             {"entries": [{"customer": "Ravi", "phone": "9811144444"}]})
    assert r["saved"] == 1
