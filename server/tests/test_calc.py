"""calculate: exact money arithmetic with nothing but numbers and operators ever evaluated."""
import asyncio
from decimal import Decimal

import pytest

from src.agent import calc
from src.agent.policy import SessionCtx, pre_tool_use
from src.agent.tools import TOOL_NAMES


def run(expression, **kw):
    return asyncio.run(calc.calculate(None, None, {"expression": expression, **kw}))


def test_the_sums_an_agent_got_wrong_in_its_head_come_out_exact():
    assert run("84500*(1-2.5%)*(1+5%)")["result"] == "86506.875"            # a low-effort model said 86,500.31
    assert run("84500*(1-2.5%)*(1+5%)")["to_2_decimals"] == "86506.88"       # half up, the way an invoice rounds
    assert run("12 x 450 x 1.18")["result"] == "6372"                         # 'x' as times, as people type it
    assert run("2,40,000 * 14% * 8/12")["result"] == "22400"                  # Indian grouping and a percent sign
    assert run("11,800/1.18")["result"] == "10000" and run("(123456+98765)/2")["result"] == "111110.5"
    assert run("Rs 1,20,000 * 18%")["in_rupees"] == "Rs 21,600.00"
    assert run("0.1+0.2")["result"] == "0.3"                                  # decimals, not floats


def test_rounding_is_half_up_and_to_the_places_asked():
    assert run("2.675", round_to=2)["rounded"] == "2.68" and run("0.5")["to_whole_rupees"] == "1" and run("1.5")["to_whole_rupees"] == "2"
    assert run("-2.5")["to_whole_rupees"] == "-3"


def test_only_arithmetic_is_ever_evaluated():
    for bad in ("__import__('os').system('x')", "open('f')", "a+1", "[1,2]", "5 +", "", "1/0", "2**0.5", "9**99", "+".join(["1"] * 90), "1;2", "True+1", "'a'*3"):
        r = run(bad)
        assert "error" in r and "result" not in r, bad
    assert "error" in run("1" + "+1" * 200)                                   # too long


def test_the_tool_is_declared_allowed_and_wired():
    assert "calculate" in TOOL_NAMES
    owner = SessionCtx("s", "t", "TRUSTED")
    assert pre_tool_use(owner, "calculate", {"expression": "1+1"}, TOOL_NAMES).verdict == "allow"
    from src.agent_loop import EXECUTORS
    assert EXECUTORS["calculate"] is calc.calculate
    assert calc.evaluate("10/4") == Decimal("2.5")
