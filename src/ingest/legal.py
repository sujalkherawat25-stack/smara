"""Public pages Meta and users need: the Smara home page, privacy, terms, and how to delete your data.

These describe what Smara actually does today (see docs/PILOT_RUNBOOK.md). Have them reviewed by a lawyer before a wide launch.

The app is mounted behind a reverse proxy that strips a path prefix, so the pages never assume where they are served:
links use PUBLIC_BASE_URL when it is set, and fall back to relative links otherwise.
"""
import html

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

router = APIRouter()
CONTACT = "Message 'delete my data' to Smara on WhatsApp"
EMAIL = "hello@syntarus.com"
UPDATED = "November 2026"

STYLE = """<style>:root{--bg:#f7f6f2;--ink:#1c1b19;--muted:#5f5a52;--accent:#0b6b4f;--card:#ffffff;--line:#e3dfd6}
@media (prefers-color-scheme:dark){:root{--bg:#151412;--ink:#eeeae4;--muted:#a49d92;--accent:#5cc79f;--card:#1e1c19;--line:#2e2b26}}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.65 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:720px;margin:0 auto;padding:20px 16px 70px}h1{font-size:26px}h2{font-size:18px;margin-top:26px}a{color:var(--accent)}
nav{display:flex;gap:16px;flex-wrap:wrap;align-items:center;padding:6px 0 18px;border-bottom:1px solid var(--line);margin-bottom:8px;font-size:15px}
nav .brand{font-weight:700;font-size:18px;color:var(--ink);text-decoration:none;margin-right:auto}
.muted{color:var(--muted);font-size:14px}
.hero{padding:26px 0 8px}.hero h1{font-size:34px;line-height:1.2;margin:0 0 10px}.hero p{font-size:18px;color:var(--muted);margin:0}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:12px;margin:14px 0}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px}.card h3{margin:0 0 4px;font-size:16px}
.card p{margin:0;color:var(--muted);font-size:15px;line-height:1.5}
.btn{display:inline-block;background:var(--accent);color:#fff;padding:10px 18px;border-radius:10px;text-decoration:none;font-weight:600;margin-top:14px}
@media (prefers-color-scheme:dark){.btn{color:#0e0d0c}}</style>"""


def _url(request: Request, path: str = "") -> str:
    base = getattr(getattr(request.app.state, "settings", None), "public_base_url", "") or ""
    return f"{base.rstrip('/')}/{path}" if base else (path or ".")


def _shell(request: Request, title: str, content: str, heading: bool = True) -> HTMLResponse:
    nav = (f'<nav><a class="brand" href="{_url(request)}">Smara</a><a href="{_url(request, "privacy")}">Privacy</a>'
           f'<a href="{_url(request, "terms")}">Terms</a><a href="{_url(request, "data-deletion")}">Delete your data</a></nav>')
    h1 = f"<h1>{html.escape(title)}</h1>" if heading else ""
    return HTMLResponse(f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<meta name="description" content="Smara is a WhatsApp assistant for Indian businesses. It helps you follow up on invoices your own customers have not paid and keeps track of what you owe, with every message approved by you first.">
{STYLE}</head><body><main>{nav}{h1}{content}
<p class="muted">Smara is a product of Syntarus. Questions: <a href="mailto:{EMAIL}">{EMAIL}</a>. Last updated {UPDATED}.</p></main></body></html>""")


@router.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return _shell(request, "Smara: your payments and back-office assistant on WhatsApp", f"""
<div class="hero"><h1>Smara</h1>
<p>An assistant on WhatsApp that helps Indian businesses follow up on invoices their own customers have not paid, and keep track of the bills they owe.
It works from your own records, and nothing goes out to your customers until you approve it.</p>
<a class="btn" href="mailto:{EMAIL}?subject=Smara%20early%20access">Ask for early access</a></div>
<h2>What Smara does</h2>
<div class="cards">
<div class="card"><h3>Follows up on payments</h3><p>Shows who owes you and how late they are, and drafts polite reminders about a specific invoice, in your customer's language. You approve each one.</p></div>
<div class="card"><h3>Reads what you send</h3><p>Send a Tally export, an Excel or CSV sheet, a PDF, a bill photo or a voice note. Smara reads it and asks you before it imports anything.</p></div>
<div class="card"><h3>Tracks what you owe</h3><p>Keeps your supplier bills and the dates they are due, including the Section 43B(h) payment limit for small suppliers, as an estimate to check with your accountant.</p></div>
<div class="card"><h3>Makes documents</h3><p>Customer statements, monthly reports and reminder letters as PDF or Word, with amounts in lakh and crore.</p></div>
<div class="card"><h3>Connects to Tally</h3><p>With the optional Smara Desktop app, your open bills are synced from Tally on your own PC every night. Your Tally login never leaves your PC.</p></div>
<div class="card"><h3>Stays in your control</h3><p>You approve every message to a customer. Smara never moves money and never asks for bank passwords, card numbers, UPI PINs or OTPs.</p></div>
</div>
<h2>What Smara is not</h2>
<p>Smara is software for your own business. It is not a debt collection agency: it does not buy or collect debts for anyone else, does not contact
people on behalf of a third party, and does not send threats or pressure. Reminders are about a specific invoice that your own customer has with you,
and go only to customers who have given you their number and agreed to hear from you on WhatsApp.</p>
<h2>How it works</h2>
<ol><li>Message Smara on WhatsApp and share your dues: a file, a photo, or a connection to Tally.</li>
<li>Smara shows you the totals and who is overdue, and drafts the reminders.</li>
<li>You approve what goes out. Customers can reply STOP at any time and will not be messaged again by your business.</li></ol>
<h2>Who it is for</h2>
<p>Traders, wholesalers and distributors in India who run their accounts on Tally or spreadsheets and chase payments by phone and WhatsApp today.</p>
<h2>Early access and support</h2>
<p>Smara is in early access with a small number of businesses, and is free during this period. To join, or to talk to a person about anything,
write to <a href="mailto:{EMAIL}?subject=Smara%20early%20access">{EMAIL}</a>. You can also read how we handle your data in the
<a href="{_url(request, "privacy")}">privacy policy</a>.</p>""", heading=False)


@router.get("/privacy", response_class=HTMLResponse)
async def privacy(request: Request):
    return _shell(request, "Smara Privacy Policy", """
<p>Smara is an assistant for businesses in India that works on WhatsApp. It helps a business follow up on invoices its own customers have not
paid, and keep track of what it owes. It is not a debt collection service. This page says what we keep and why.</p>
<h2>Who is responsible for what</h2>
<p>The business that uses Smara decides which customer and supplier details to share, and is responsible to its own customers for that.
Syntarus runs Smara for the business and handles that information only to provide the service.</p>
<h2>What we collect</h2>
<ul><li><strong>From the business owner:</strong> the shop name, WhatsApp number, UPI ID (if given), and the messages, voice notes, photos and files
they send Smara (for example an outstanding-dues sheet, a Tally export, a PDF or a bill photo), and what Smara replies.</li>
<li><strong>From Smara Desktop, only if the owner turns it on:</strong> the open bills the business is owed and the open bills it has to pay, and the
names and phone numbers of those parties as saved in Tally. We do not copy the rest of the Tally company file, and we never receive Tally passwords.
Logins the owner uses in the Smara browser helper stay on the owner's own PC and are never sent to us.</li>
<li><strong>About the owner's customers and suppliers:</strong> the name, phone number, bill amounts and dates the owner shares, and messages those
customers send to the shop's Smara number.</li></ul>
<h2>What we do with it</h2>
<ul><li>Answer the owner's questions, prepare statements, reports and reminders (including as PDF or Word files), and remember promises and
notes so the owner does not have to.</li>
<li>Customers are messaged only after the owner approves each message, only about an unpaid invoice they have with that business, and only if
they have agreed to hear from the business on WhatsApp. A customer can reply STOP at any time and will not be messaged again by that shop.</li>
<li>We never move money and never store bank passwords, card numbers, UPI PINs or OTPs.</li>
<li>We do not use customer details for anything other than the business's own messages: no marketing, no sharing between businesses.</li></ul>
<h2>Who processes it</h2>
<p>Our server in India; an AI model provider (Sarvam), which reads message text and the text of files and photos, and turns voice notes into text,
to answer; Syntarus's own memory service, which keeps notes and promises; and WhatsApp (Meta), which delivers messages and files.
We do not sell data or use it for advertising.</p>
<h2>How long</h2>
<p>Until the business leaves Smara or asks us to delete it. Nightly backups are kept for 7 days.</p>
<h2>Your rights</h2>
<p>You can see your data and ask us to delete it. """ + CONTACT + """, or open the <a href="data-deletion">data deletion page</a>.
You can also write to <a href="mailto:""" + EMAIL + """">""" + EMAIL + """</a>.</p>
<h2>Security</h2>
<p>Each shop's access keys are encrypted with a key that is stored separately from the database. Shops cannot see each other's data.</p>""")


@router.get("/terms", response_class=HTMLResponse)
async def terms(request: Request):
    return _shell(request, "Smara Terms of Use", """
<p>By using Smara you agree to these terms.</p>
<h2>What Smara is</h2>
<p>An assistant that reads your business records and the files you send, answers questions, and drafts reminders, statements and reports.
It can make mistakes: check amounts before you approve anything. Smara never sends a message to your customers, changes an invoice, or does
anything on a website without your approval.</p>
<h2>Your responsibilities</h2>
<ul><li>Share only customer information you are allowed to share, and message customers only about unpaid invoices they have with you.</li>
<li>Message only customers who have given you their number and agreed to hear from you on WhatsApp, and stop when they ask you to.</li>
<li>Use Smara for your own business's invoices only, not to collect debts for anyone else, and never to threaten or pressure anyone.</li>
<li>Do not use Smara to harass anyone or to send misleading messages.</li>
<li>Keep your own phone and accounts secure.</li>
<li>Check any document Smara makes before you share it. Dates and tax deadlines it shows, such as Section 43B(h) payment dates, are
estimates from the data you gave it, so confirm them with your accountant.</li></ul>
<h2>Limits</h2>
<p>Smara is provided as it is during the early access period, with daily usage limits. We may pause an account that breaks these terms.
Smara is not a financial, legal or tax adviser.</p>
<h2>Ending</h2>
<p>You can stop at any time and ask us to delete your data (see the privacy policy).</p>""")


@router.get("/data-deletion", response_class=HTMLResponse)
async def data_deletion(request: Request):
    return _shell(request, "Delete your Smara data", """
<p>To delete your business data from Smara:</p>
<ol><li>Open WhatsApp and message Smara: <strong>delete my data</strong>.</li>
<li>Smara records your request and confirms it. We delete your shop, your customer list, dues, messages and saved notes within 7 days,
and then tell you it is done.</li>
<li>Backups made before that are removed automatically within 7 days.</li></ol>
<p>A customer of a shop who wants their number removed can reply <strong>STOP</strong> to any message, and that shop will not message them again.</p>""")
