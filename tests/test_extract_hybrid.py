from datetime import date

from src.ingest.extract import extract_claim

TODAY = date(2026, 11, 3)
OUT = 5_000_000


class FakeLLM:
    enabled = True

    def __init__(self, reply=None, boom=False):
        self.reply, self.boom, self.system = reply or {}, boom, ""

    async def json(self, system, user, **kw):
        self.system = system
        if self.boom:
            raise ConnectionError("llm down")
        return self.reply


async def run(text, llm):
    return await extract_claim(llm, text, outstanding_paise=OUT, today=TODAY)


async def test_rules_supply_the_numbers_the_model_cannot():
    llm = FakeLLM({"type": "DISCOUNT_CLAIM", "new_amount_rupees": None, "promise_date": None})
    c = await run("10% discount de do", llm)
    assert (c.type, c.amount_paise) == ("DISCOUNT_CLAIM", 4_500_000)
    assert "50000" in llm.system                       # the model is told the outstanding amount


async def test_rules_override_a_conflicting_model_number():
    c = await run("5000 kam kar do", FakeLLM({"type": "DISCOUNT_CLAIM", "new_amount_rupees": 1}))
    assert c.amount_paise == 4_500_000


async def test_model_figure_used_only_when_rules_find_none_and_it_is_sane():
    ok = await run("kuch discount de do, 42000 theek rahega", FakeLLM({"type": "DISCOUNT_CLAIM", "new_amount_rupees": 42000}))
    assert ok.amount_paise in (4_200_000, None)
    insane = await run("thoda discount do", FakeLLM({"type": "DISCOUNT_CLAIM", "new_amount_rupees": 90000}))
    assert insane.amount_paise is None                 # a "discount" can never raise the total


async def test_model_recovers_a_vague_promise_and_today_is_not_a_date():
    vague = await run("Diwali ke baad dekhte hain", FakeLLM({"type": "PROMISE_DATE_CHANGE", "promise_date": "2026-11-03"}))
    assert (vague.type, vague.date) == ("PROMISE_DATE_CHANGE", None)
    future = await run("jaldi karunga", FakeLLM({"type": "PROMISE_DATE_CHANGE", "promise_date": "2026-11-20"}))
    assert future.date == date(2026, 11, 20)


async def test_model_says_no_claim_but_rules_do_keeps_the_claim():
    c = await run("kal tak bhej dunga", FakeLLM({"type": None}))
    assert c.type == "PROMISE_DATE_CHANGE" and c.date == date(2026, 11, 4)


async def test_no_claim_from_either_is_none_and_outage_falls_back_to_rules():
    assert await run("namaste", FakeLLM({"type": None})) is None
    c = await run("10% kam kar do", FakeLLM(boom=True))
    assert c.amount_paise == 4_500_000
