"""Connect a shop's OWN WhatsApp Business number to Smara (Meta Embedded Signup).

Flow:
  owner writes "connect whatsapp" in their chat  ->  one-time link (24 h)  ->  this page  ->  Meta's own popup
  (log in with Facebook, pick the business and number; an existing WhatsApp Business app number keeps working on the
  phone: "coexistence")  ->  the page posts Meta's one-time code here  ->  we exchange it for that shop's token
  (kept only in the encrypted vault), subscribe our webhook to their account, register the number if it is new,
  create our message templates in their account, and tell the owner it is done.

Needs, in the server .env: META_APP_ID, WA_APP_SECRET (already there) and META_ES_CONFIG_ID (the Embedded Signup
configuration made in the Meta app). Until those exist the page says so instead of failing.
"""
import hashlib
import html
import json
import logging
import secrets
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from src import tenants, vault

log = logging.getLogger(__name__)
router = APIRouter()
GRAPH_VERSION = "v25.0"
GRAPH = f"https://graph.facebook.com/{GRAPH_VERSION}"
LINK_TTL = timedelta(hours=24)
TOKEN_SECRET = "whatsapp_token"
PIN_SECRET = "whatsapp_2fa_pin"


def _hash(v: str) -> str:
    return hashlib.sha256(v.encode()).hexdigest()


def configured(settings) -> bool:
    return bool(getattr(settings, "meta_app_id", "") and getattr(settings, "meta_es_config_id", "")
                and getattr(settings, "wa_app_secret", ""))


async def new_link(pool, settings, tenant_id: str, path: str = "/connect") -> str:
    """A one-time link (24 h) the owner opens to connect a number. `path` picks the page (Meta flow or SIM link)."""
    token = secrets.token_urlsafe(24)
    async with pool.connection() as conn:
        await conn.execute("DELETE FROM whatsapp_connect_links WHERE tenant_id = %s AND used_at IS NULL", (tenant_id,))
        await conn.execute("INSERT INTO whatsapp_connect_links (token_hash, tenant_id, expires_at) VALUES (%s, %s, %s)",
                           (_hash(token), tenant_id, datetime.now(timezone.utc) + LINK_TTL))
    return f"{settings.public_base_url.rstrip('/')}{path}?t={token}"


async def _link_tenant(pool, token: str, consume: bool):
    async with pool.connection() as conn:
        if consume:
            row = await (await conn.execute(
                """UPDATE whatsapp_connect_links SET used_at = now()
                    WHERE token_hash = %s AND used_at IS NULL AND expires_at > now() RETURNING tenant_id""",
                (_hash(token),))).fetchone()
        else:
            row = await (await conn.execute(
                "SELECT tenant_id FROM whatsapp_connect_links WHERE token_hash = %s AND used_at IS NULL AND expires_at > now()",
                (_hash(token),))).fetchone()
    return await tenants.by_id(pool, str(row["tenant_id"])) if row else None


PAGE = """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Connect WhatsApp to Smara</title>
<style>
:root{--bg:#f7f6f2;--ink:#1c1b19;--muted:#5f5a52;--line:#e1dbd0;--accent:#0b6b4f;--soft:#e2f1ea;--bad:#a3262d}
@media (prefers-color-scheme:dark){:root{--bg:#151412;--ink:#eeeae4;--muted:#a49d92;--line:#35312b;--accent:#5cc79f;--soft:#183329;--bad:#ec7f86}}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:560px;margin:0 auto;padding:28px 16px 60px}h1{font-size:24px;margin:0 0 6px}p{margin:8px 0}
.card{background:transparent;border:1px solid var(--line);border-radius:12px;padding:16px;margin:16px 0}
button{display:block;width:100%;padding:14px;margin:10px 0;border:0;border-radius:10px;font-size:16px;font-weight:600;cursor:pointer;
background:var(--accent);color:#fff}button.alt{background:transparent;color:var(--accent);border:1.5px solid var(--accent)}
button:disabled{opacity:.5;cursor:default}.muted{color:var(--muted);font-size:14px}.ok{background:var(--soft);border-color:var(--accent)}
.err{color:var(--bad)}ul{padding-left:20px}
</style></head><body><main>
<h1>Connect your WhatsApp to Smara</h1>
<p>Shop: <strong>__SHOP__</strong></p>
<div class="card">
<p><strong>What happens</strong></p>
<ul><li>Meta's own window opens. You log in with Facebook and choose your business and its WhatsApp number.</li>
<li>Your customers will see messages from <strong>your</strong> number, not from Smara's.</li>
<li>Smara never sends anything to a customer without your tap, and you can disconnect any time.</li></ul>
</div>
<button id="coex" __DISABLED__>Use my existing WhatsApp Business app number</button>
<p class="muted">Recommended. You keep using the WhatsApp Business app on your phone as before.</p>
<button id="cloud" class="alt" __DISABLED__>Use a new number</button>
<div id="status" class="card" style="display:none"></div>
<p class="muted">__NOTE__</p>
</main>
<script>
const CFG = __CFG__;
const out = document.getElementById('status');
let session = {};
function show(html, cls){ out.style.display='block'; out.className='card '+(cls||''); out.innerHTML=html; }
window.addEventListener('message', e => {
  if (!/facebook\\.com$/.test(new URL(e.origin).hostname)) return;
  try { const d = JSON.parse(e.data); if (d.type === 'WA_EMBEDDED_SIGNUP') session = d; } catch (_) {}
});
async function finish(code, mode){
  show('Connecting your number, please wait...');
  const body = {t: CFG.token, code, mode, waba_id: (session.data||{}).waba_id || '', phone_number_id: (session.data||{}).phone_number_id || '',
                event: session.event || ''};
  try {
    const r = await fetch('connect/complete', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
    const j = await r.json();
    if (r.ok) show('<strong>Connected.</strong> '+(j.message||'')+'<br><span class="muted">You can close this page and go back to WhatsApp.</span>', 'ok');
    else show('<span class="err">'+(j.detail||'Could not connect.')+'</span>');
  } catch (err) { show('<span class="err">Network error. Please try again.</span>'); }
}
function start(mode){
  if (!window.FB) { show('<span class="err">Meta\\'s window could not load. Check your internet and reload.</span>'); return; }
  const extras = {setup:{}, sessionInfoVersion:'3'};
  if (mode === 'coexistence') extras.featureType = 'whatsapp_business_app_onboarding';
  FB.login(resp => {
    if (resp.authResponse && resp.authResponse.code) finish(resp.authResponse.code, mode);
    else show('<span class="err">Connection was cancelled. Nothing was changed.</span>');
  }, {config_id: CFG.config_id, response_type:'code', override_default_response_type:true, extras});
}
document.getElementById('coex').onclick = () => start('coexistence');
document.getElementById('cloud').onclick = () => start('cloud');
if (CFG.app_id) {
  window.fbAsyncInit = () => FB.init({appId: CFG.app_id, autoLogAppEvents: true, xfbml: false, version: CFG.version});
  const s = document.createElement('script'); s.async = true; s.crossOrigin = 'anonymous';
  s.src = 'https://connect.facebook.net/en_US/sdk.js'; document.head.appendChild(s);
}
</script></body></html>"""


def _page(shop: str, cfg: dict, note: str, disabled: bool) -> str:
    return (PAGE.replace("__SHOP__", html.escape(shop)).replace("__CFG__", json.dumps(cfg))
            .replace("__NOTE__", html.escape(note)).replace("__DISABLED__", "disabled" if disabled else ""))


@router.get("/connect", response_class=HTMLResponse)
async def connect_page(request: Request, t: str = ""):
    app = request.app
    tenant = await _link_tenant(app.state.pool, t, consume=False) if t else None
    if tenant is None:
        return HTMLResponse(_page("-", {}, "This link has expired or was already used. Send 'connect whatsapp' to Smara "
                                           "for a new one.", True), status_code=404)
    s = app.state.settings
    if not configured(s):
        return HTMLResponse(_page(tenant.name, {}, "Connecting your own number is not switched on yet. We will tell you "
                                                   "when it is.", True))
    cfg = {"token": t, "app_id": s.meta_app_id, "config_id": s.meta_es_config_id, "version": GRAPH_VERSION}
    return HTMLResponse(_page(tenant.name, cfg, "Takes about two minutes. Keep your phone nearby for the code Meta sends.", False))


class CompleteBody(BaseModel):
    t: str = Field(min_length=10, max_length=100)
    code: str = Field(min_length=10, max_length=2000)
    mode: str = Field(default="coexistence", pattern="^(coexistence|cloud)$")
    waba_id: str = Field(default="", max_length=40)
    phone_number_id: str = Field(default="", max_length=40)
    event: str = Field(default="", max_length=60)


async def _graph(http: httpx.AsyncClient, method: str, path: str, token: str, **kw) -> dict:
    r = await http.request(method, f"{GRAPH}/{path.lstrip('/')}", headers={"Authorization": f"Bearer {token}"}, **kw)
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if r.status_code >= 400:
        raise HTTPException(status_code=400, detail=f"Meta refused: {((body.get('error') or {}).get('message') or r.text)[:200]}")
    return body


async def _record(pool, tenant_id: str, waba: str, number: str, mode: str, status: str, detail: dict) -> None:
    async with pool.connection() as conn:
        await conn.execute("""INSERT INTO whatsapp_connections (tenant_id, waba_id, phone_number_id, mode, status, detail)
                              VALUES (%s, %s, %s, %s, %s, %s)""", (tenant_id, waba, number, mode, status, json.dumps(detail)))


@router.post("/connect/complete")
async def connect_complete(body: CompleteBody, request: Request):
    app = request.app
    pool, s = app.state.pool, app.state.settings
    if not configured(s):
        raise HTTPException(status_code=503, detail="Connecting numbers is not switched on yet.")
    if not (body.waba_id.isdigit() and body.phone_number_id.isdigit()):
        raise HTTPException(status_code=400, detail="Meta did not tell us which number you picked. Please try again.")
    tenant = await _link_tenant(pool, body.t, consume=True)          # single use from here on
    if tenant is None:
        raise HTTPException(status_code=400, detail="This link has expired or was already used. Ask Smara for a new one.")
    http = getattr(app.state, "graph_http", None) or httpx.AsyncClient(timeout=30)
    detail: dict = {"event": body.event}
    try:
        tok = await http.get(f"{GRAPH}/oauth/access_token", params={
            "client_id": s.meta_app_id, "client_secret": s.wa_app_secret, "code": body.code})
        token = (tok.json() if tok.status_code == 200 else {}).get("access_token")
        if not token:
            raise HTTPException(status_code=400, detail="Meta did not accept the sign-in. Please try again.")
        # the token must really see the account and number the page reported (never trust the browser for this)
        await _graph(http, "GET", body.waba_id, token, params={"fields": "id,name"})
        num = await _graph(http, "GET", body.phone_number_id, token, params={"fields": "display_phone_number,verified_name"})
        async with pool.connection() as conn:
            other = await (await conn.execute("SELECT id FROM tenants WHERE wa_phone_number_id = %s AND id <> %s",
                                              (body.phone_number_id, tenant.id))).fetchone()
        if other:
            raise HTTPException(status_code=409, detail="This number is already connected to another shop on Smara.")
        await _graph(http, "POST", f"{body.waba_id}/subscribed_apps", token)          # their messages reach our webhook
        if body.mode == "cloud":                    # a brand-new Cloud API number must be registered once, with a PIN
            pin = f"{secrets.randbelow(10**6):06d}"
            await _graph(http, "POST", f"{body.phone_number_id}/register", token,
                         json={"messaging_product": "whatsapp", "pin": pin})
            await vault.put_secret(pool, tenant.id, PIN_SECRET, pin)
        await vault.put_secret(pool, tenant.id, TOKEN_SECRET, token)
        async with pool.connection() as conn:
            await conn.execute("""UPDATE tenants SET wa_phone_number_id = %s, wa_waba_id = %s, wa_display_phone = %s,
                                         wa_connected_at = now() WHERE id = %s""",
                               (body.phone_number_id, body.waba_id, num.get("display_phone_number", ""), tenant.id))
        forget_token(body.phone_number_id)
        detail["templates"] = await create_templates(http, body.waba_id, token)
        await _record(pool, tenant.id, body.waba_id, body.phone_number_id, body.mode, "connected", detail)
    except HTTPException as e:
        await _record(pool, tenant.id, body.waba_id, body.phone_number_id, body.mode, "failed", {**detail, "error": e.detail})
        raise
    finally:
        if not getattr(app.state, "graph_http", None):
            await http.aclose()
    shown = num.get("display_phone_number", "your number")
    try:
        from src.loop.owner_channel import send_to_owner
        from src.loop.whatsapp import text_message
        t = await tenants.by_id(pool, tenant.id)
        await send_to_owner(app, t, text_message(t.owner_phone, f"Your WhatsApp number {shown} is now connected to Smara. "
                                                 "Your customers will see messages from your own number, and nothing goes "
                                                 "to them without your tap."), kind="owner_note")
    except Exception:
        log.warning("could not tell the owner about the new connection", exc_info=True)
    return {"connected": True, "message": f"{shown} is now connected to {tenant.name}."}


async def create_templates(http, waba_id: str, token: str) -> dict:
    """Our standard templates in the shop's own account (Meta reviews them per account). Best effort."""
    from src.loop.templates import TEMPLATES_V3
    out = {}
    for t in TEMPLATES_V3:
        try:
            r = await http.post(f"{GRAPH}/{waba_id}/message_templates", json=t, headers={"Authorization": f"Bearer {token}"})
            out[f"{t['name']}:{t['language']}"] = r.json().get("status") or r.json().get("error", {}).get("message", "")[:80]
        except Exception as e:
            out[f"{t['name']}:{t['language']}"] = f"error {type(e).__name__}"
    return out


# ---------------------------------------------------------------- per-shop tokens for sending and media

_TOKENS: dict[str, tuple[float, str | None]] = {}
TOKEN_CACHE_S = 300


def forget_token(number_id: str) -> None:
    _TOKENS.pop(number_id, None)


async def token_for_number(pool, number_id: str) -> str | None:
    """The shop's own token for one of its numbers, from the vault (cached briefly). None = use Smara's own token."""
    import time
    if not number_id:
        return None
    hit = _TOKENS.get(number_id)
    if hit and time.monotonic() - hit[0] < TOKEN_CACHE_S:
        return hit[1]
    t = await tenants.by_number_id(pool, number_id)
    tok = None
    if t:
        try:
            raw = await vault.get_secret(pool, t.id, TOKEN_SECRET)
            tok = raw.decode() if raw else None
        except Exception:
            log.warning("vault unavailable for number %s", number_id[-4:])
    _TOKENS[number_id] = (time.monotonic(), tok)
    return tok
