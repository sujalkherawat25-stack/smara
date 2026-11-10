"""Pairing page for a shop's own SIM (the Baileys link), plus the internal endpoints the sidecar calls back on.

The owner asks for "connect whatsapp" and gets the same one-time link as the Meta flow (connect.new_link), but with
this page: it shows a QR code, the owner scans it from the SIM's WhatsApp, and the page reports connected or not.
The link stays usable for 15 minutes after it connects, so the page can finish showing "connected".
"""
import hashlib
import hmac
import html
import json
import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from src import tenants
from src.config import normalize_phone
from src.ingest.dispatch import dispatch
from src.ingest.payload import MEDIA_KINDS, InboundMessage
from src.loop import wa_link

log = logging.getLogger(__name__)
router = APIRouter()
GRACE_AFTER_CONNECT = "15 minutes"

PAGE = """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Link your SIM to Smara</title>
<style>
:root{--bg:#f7f6f2;--ink:#1c1b19;--muted:#5f5a52;--line:#e1dbd0;--accent:#0b6b4f;--soft:#e2f1ea;--bad:#a3262d;--warn:#8a5a00}
@media (prefers-color-scheme:dark){:root{--bg:#151412;--ink:#eeeae4;--muted:#a49d92;--line:#35312b;--accent:#5cc79f;--soft:#183329;--bad:#ec7f86;--warn:#e7b96a}}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:560px;margin:0 auto;padding:28px 16px 60px}h1{font-size:24px;margin:0 0 6px}p{margin:8px 0}
.card{border:1px solid var(--line);border-radius:12px;padding:16px;margin:16px 0}
.qr{background:#fff;border-radius:12px;padding:12px;width:260px;max-width:100%;margin:12px auto}.qr svg{width:100%;height:auto;display:block}
.muted{color:var(--muted);font-size:14px}.ok{background:var(--soft);border-color:var(--accent)}.warn{color:var(--warn)}.err{color:var(--bad)}
button{display:block;width:100%;padding:14px;margin:10px 0;border:0;border-radius:10px;font-size:16px;font-weight:600;cursor:pointer;background:var(--accent);color:#fff}
ul{padding-left:20px}
</style></head><body><main>
<h1>Link your SIM to Smara</h1>
<p>Shop: <strong>__SHOP__</strong></p>
<div class="card">
<p><strong>Before you scan</strong></p>
<ul><li>Use the SIM phone that will send your customer reminders. It must have WhatsApp (not WhatsApp Business) on it.</li>
<li>Smara sends a customer message only after you approve it. Nothing goes out at night.</li>
<li>If WhatsApp ever logs this number out, all sending pauses until you link it again.</li></ul>
</div>
<div id="panel" class="card"><p>Getting the code...</p></div>
<button id="again" style="display:none">Get a new code</button>
<p class="muted" id="note"></p>
</main>
<script>
const CFG = __CFG__;
const panel = document.getElementById('panel'), note = document.getElementById('note'), again = document.getElementById('again');
let timer = null;
function show(html, cls){ panel.className = 'card ' + (cls || ''); panel.innerHTML = html; }
async function call(path){
  const r = await fetch(path, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({t: CFG.token})});
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail || 'Could not reach Smara.');
  return j;
}
let polls = 0, finished = false;
function finish(){ finished = true; if (timer) { clearInterval(timer); timer = null; } }
function fail(msg){ finish(); show('<span class="err">' + msg + '</span>'); again.style.display = 'block'; }
function render(s){
  again.style.display = 'none';
  if (s.status === 'connected') {
    finish();
    show('<strong>Connected.</strong> Your number ' + (s.phone ? '+' + s.phone : '') + ' is linked to Smara.<br><span class="muted">You can close this page and go back to WhatsApp.</span>', 'ok');
  } else if (s.status === 'pairing' && s.qr_svg) {
    show('<div class="qr">' + s.qr_svg + '</div><p>' + s.message + '</p><p class="muted">The code refreshes every few seconds.</p>');
  } else if (s.status === 'pairing') {
    show('Getting the code...');
  } else if (s.status === 'reconnecting' || s.status === 'connecting') {
    show('<span class="warn">Connecting to WhatsApp...</span>');
  } else {
    fail(s.message || 'Not connected.');
  }
}
async function poll(){
  if (finished) return;
  if (++polls > 100) { fail('Still not connected. Get a new code and try again.'); return; }   // about four minutes
  try { render(await call('wa-link/state')); }
  catch (err) { fail(err.message); }
}
async function begin(){
  finished = false; polls = 0;
  again.style.display = 'none';
  try { render(await call('wa-link/start')); } catch (err) { fail(err.message); return; }
  if (!finished && !timer) timer = setInterval(poll, 2500);
}
again.onclick = begin;
if (CFG.error) { show(CFG.error, CFG.errorClass || 'err'); again.style.display = 'none'; } else { begin(); }
</script></body></html>"""


def _page(shop: str, cfg: dict) -> str:
    return PAGE.replace("__SHOP__", html.escape(shop)).replace("__CFG__", json.dumps(cfg))


async def _linked_tenant(pool, token: str):
    """The shop a pairing link belongs to: valid for 24 hours, and for 15 minutes after the link has connected."""
    async with pool.connection() as conn:
        row = await (await conn.execute(
            f"""SELECT tenant_id FROM whatsapp_connect_links WHERE token_hash = %s AND expires_at > now()
                AND (used_at IS NULL OR used_at > now() - interval '{GRACE_AFTER_CONNECT}')""",
            (hashlib.sha256(token.encode()).hexdigest(),))).fetchone()
    return await tenants.by_id(pool, str(row["tenant_id"])) if row else None


@router.get("/connect/wa-link", response_class=HTMLResponse)
async def wa_link_page(request: Request, t: str = ""):
    app = request.app
    tenant = await _linked_tenant(app.state.pool, t) if t else None
    if tenant is None:
        return HTMLResponse(_page("-", {"error": "This link has expired or was already used. Send 'connect whatsapp' "
                                                 "to Smara for a new one."}), status_code=404)
    if not wa_link.configured(app):
        return HTMLResponse(_page(tenant.name, {"error": "Linking your own number is not switched on yet.",
                                                "errorClass": "warn"}), status_code=503)
    return HTMLResponse(_page(tenant.name, {"token": t}))


class TokenBody(BaseModel):
    t: str = Field(min_length=10, max_length=100)


async def _page_state(request: Request, body: TokenBody, *, start: bool) -> dict:
    app = request.app
    tenant = await _linked_tenant(app.state.pool, body.t)
    if tenant is None:
        raise HTTPException(status_code=404, detail="This link has expired or was already used. Ask Smara for a new one.")
    if not wa_link.configured(app):
        raise HTTPException(status_code=503, detail="Linking your own number is not switched on yet.")
    try:
        if start:
            await wa_link.begin_pairing(app, str(tenant.id))
        return await wa_link.view(app, str(tenant.id))
    except wa_link.SidecarError:
        raise HTTPException(status_code=503, detail="The link service is not reachable right now. Try again in a minute.")


@router.post("/connect/wa-link/state")
async def wa_link_state(body: TokenBody, request: Request):
    return await _page_state(request, body, start=False)


@router.post("/connect/wa-link/start")
async def wa_link_start(body: TokenBody, request: Request):
    return await _page_state(request, body, start=True)


# ---------------------------------------------------------------- internal: called by the sidecar only

def _require_sidecar(request: Request) -> None:
    expected = request.app.state.settings.wa_link_token
    got = request.headers.get("authorization", "")
    if not expected or not hmac.compare_digest(got, f"Bearer {expected}"):
        raise HTTPException(status_code=401, detail="unauthorized")


class StateBody(BaseModel):
    tenant_id: str = Field(min_length=36, max_length=36)
    auth: str = Field(min_length=2, max_length=5_000_000)


class StatusBody(BaseModel):
    tenant_id: str = Field(min_length=36, max_length=36)
    status: str = Field(min_length=3, max_length=20)
    phone: str = Field(default="", max_length=20)


class MediaBody(BaseModel):
    """A file or voice note on the SIM. The bytes stay in the sidecar until the app asks for them (loop/wa_link.media)."""
    ref: str | None = Field(default=None, max_length=64)     # None: too large to hold, so it is refused by size
    mime: str = Field(default="", max_length=120)
    filename: str = Field(default="", max_length=255)
    size: int = Field(default=0, ge=0, le=10**10)


class InboundBody(BaseModel):
    tenant_id: str = Field(min_length=36, max_length=36)
    wamid: str = Field(min_length=3, max_length=120)
    sender: str = Field(min_length=6, max_length=20)
    kind: str = Field(default="text", max_length=20)
    text: str = Field(default="", max_length=4000)
    ts: int = 0
    media: MediaBody | None = None


async def _known_tenant(request: Request, tenant_id: str):
    tenant = await tenants.by_id(request.app.state.pool, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="unknown shop")
    return tenant


@router.post("/internal/wa-link/state")
async def internal_state(body: StateBody, request: Request):
    _require_sidecar(request)
    try:
        creds = json.loads(body.auth).get("creds")
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail="bad auth state")
    if not isinstance(creds, dict):
        raise HTTPException(status_code=400, detail="bad auth state")
    tenant = await _known_tenant(request, body.tenant_id)
    await wa_link.on_state(request.app.state.pool, str(tenant.id), body.auth)
    return {"ok": True}


@router.post("/internal/wa-link/status")
async def internal_status(body: StatusBody, request: Request):
    _require_sidecar(request)
    tenant = await _known_tenant(request, body.tenant_id)
    try:
        await wa_link.on_status(request.app, str(tenant.id), body.status, body.phone)
    except ValueError:
        raise HTTPException(status_code=400, detail="unknown status")
    return {"ok": True}


@router.post("/internal/wa-link/inbound")
async def internal_inbound(body: InboundBody, request: Request):
    """A customer wrote to the shop's SIM. It goes through the same pipeline as a message on the shared number."""
    _require_sidecar(request)
    tenant = await _known_tenant(request, body.tenant_id)
    sender = normalize_phone(body.sender)
    if not sender:
        raise HTTPException(status_code=400, detail="no sender")
    if body.media is not None and body.kind in MEDIA_KINDS:        # a file or voice note: its caption is the text
        msg = InboundMessage(wamid=body.wamid, sender=sender, kind=body.kind, caption=body.text, media_id=body.media.ref,
                             mime=body.media.mime, filename=body.media.filename, size=body.media.size,
                             channel="wa_link", link_tenant_id=str(tenant.id))
    else:
        msg = InboundMessage(wamid=body.wamid, sender=sender, kind=body.kind or "text", text=body.text,
                             channel="wa_link", link_tenant_id=str(tenant.id))
    await dispatch(request.app, msg)     # on an error this raises: the sidecar retries, and dispatch allows a redelivery
    return {"ok": True}
