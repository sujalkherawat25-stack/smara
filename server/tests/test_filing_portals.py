"""Smara never drives a government filing portal: login, OTP and digital signature belong to the owner or their CA."""
import pytest

from src.agent.policy import SessionCtx, filing_portal, pre_tool_use
from src.agent.tools import TOOL_NAMES

CTX = SessionCtx("s", "t", "TRUSTED")


@pytest.mark.parametrize("url", ["https://services.gst.gov.in/services/login", "https://return.gst.gov.in/returns/auth/gstr3b", "https://www.gst.gov.in/login", "https://www.gst.gov.in/",
                                 "https://eportal.incometax.gov.in/iec/foservices/", "tdscpc.gov.in", "https://unifiedportal-emp.epfindia.gov.in/epfo/",
                                 "https://www.mca.gov.in/mcafoportal/login.do"])
def test_filing_and_login_pages_are_refused_with_a_reason_the_agent_can_pass_on(url):
    d = pre_tool_use(CTX, "browser_step", {"action": "navigate", "url": url}, TOOL_NAMES)
    assert d.verdict == "deny" if hasattr(d, "verdict") else d.action == "deny"
    assert "never signs in" in d.reason and "prepare" in d.reason


@pytest.mark.parametrize("url", ["https://taxsalahkars.com/blog/gst", "https://www.incometax.gov.in/iec/foportal/help",
                                 "https://cbic-gst.gov.in/gst-goods-services-rates.html"])
def test_public_pages_can_still_be_read(url):
    assert not filing_portal(url)
    d = pre_tool_use(CTX, "browser_step", {"action": "navigate", "url": url}, TOOL_NAMES)
    assert "never signs in" not in (d.reason or "")
