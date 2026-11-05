"""Public pages Meta and users need: privacy, terms, and how to delete your data.

These describe what Smara actually does today (see docs/PILOT_RUNBOOK.md). Have them reviewed by a lawyer before a wide launch.
"""
import html

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()
CONTACT = "Message 'delete my data' to Smara on WhatsApp"

STYLE = """<style>:root{--bg:#f7f6f2;--ink:#1c1b19;--muted:#5f5a52;--accent:#0b6b4f}
@media (prefers-color-scheme:dark){:root{--bg:#151412;--ink:#eeeae4;--muted:#a49d92;--accent:#5cc79f}}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.65 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:720px;margin:0 auto;padding:28px 16px 70px}h1{font-size:26px}h2{font-size:18px;margin-top:26px}a{color:var(--accent)}
.muted{color:var(--muted);font-size:14px}</style>"""


def _page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>{STYLE}</head><body><main><h1>{html.escape(title)}</h1>{body}
<p class="muted">Smara is a product of Syntarus. Last updated November 2026.</p></main></body></html>""")


@router.get("/privacy", response_class=HTMLResponse)
async def privacy():
    return _page("Smara Privacy Policy", """
<p>Smara is an assistant for small businesses in India that works on WhatsApp. This page says what we keep and why.</p>
<h2>What we collect</h2>
<ul><li><strong>From the business owner:</strong> the shop name, WhatsApp number, UPI ID (if given), the messages and files they send Smara
(for example an outstanding-dues sheet or a Tally export), and what Smara replies.</li>
<li><strong>About the owner's customers:</strong> the name, phone number and dues the owner shares, and messages those customers send
to the shop's Smara number.</li></ul>
<h2>What we do with it</h2>
<ul><li>Answer the owner's questions, prepare statements and reminders, and remember promises and notes so the owner does not have to.</li>
<li>Customers are messaged only after the owner approves each message, and only about dues. A customer can reply STOP at any time and
will not be messaged again by that shop.</li>
<li>We never move money and never store bank passwords, card numbers, UPI PINs or OTPs.</li></ul>
<h2>Who processes it</h2>
<p>Our server in India, an AI model provider (Sarvam) that reads message text to answer, and WhatsApp (Meta) to deliver messages.
We do not sell data or use it for advertising.</p>
<h2>How long</h2>
<p>Until the business leaves Smara or asks us to delete it. Nightly backups are kept for 7 days.</p>
<h2>Your rights</h2>
<p>You can see your data and ask us to delete it. """ + CONTACT + """, or open the <a href="data-deletion">data deletion page</a>.</p>
<h2>Security</h2>
<p>Each shop's access keys are encrypted with a key that is stored separately from the database. Shops cannot see each other's data.</p>""")


@router.get("/terms", response_class=HTMLResponse)
async def terms():
    return _page("Smara Terms of Use", """
<p>By using Smara you agree to these terms.</p>
<h2>What Smara is</h2>
<p>An assistant that reads your business records, answers questions, and drafts reminders and statements. It can make mistakes:
check amounts before you approve anything. Smara never sends a message to your customers, changes an invoice, or does anything on
a website without your approval.</p>
<h2>Your responsibilities</h2>
<ul><li>Share only customer information you are allowed to share, and message customers only about dues they owe you.</li>
<li>Do not use Smara to harass anyone or to send misleading messages.</li>
<li>Keep your own phone and accounts secure.</li></ul>
<h2>Limits</h2>
<p>Smara is provided as it is during the early access period, with daily usage limits. We may pause an account that breaks these terms.
Smara is not a financial, legal or tax adviser.</p>
<h2>Ending</h2>
<p>You can stop at any time and ask us to delete your data (see the privacy policy).</p>""")


@router.get("/data-deletion", response_class=HTMLResponse)
async def data_deletion():
    return _page("Delete your Smara data", """
<p>To delete your business data from Smara:</p>
<ol><li>Open WhatsApp and message Smara: <strong>delete my data</strong>.</li>
<li>Smara records your request and confirms it. We delete your shop, your customer list, dues, messages and saved notes within 7 days,
and then tell you it is done.</li>
<li>Backups made before that are removed automatically within 7 days.</li></ol>
<p>A customer of a shop who wants their number removed can reply <strong>STOP</strong> to any message, and that shop will not message them again.</p>""")
