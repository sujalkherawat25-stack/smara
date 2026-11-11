#!/usr/bin/env python3
"""External watchdog for Smara. Runs on the HOST (stdlib only), every minute from cron.

    python3 scripts/watchdog.py            # check, alert on change, exit 0 even when something is down

It checks what a customer or Meta would actually hit, not just the container:
  - the PUBLIC webhook route answers like our app (a bad verify token must give 403; a redirect or 5xx means the
    proxy route was lost, which is exactly how webhooks went dead for hours once)
  - /healthz: database, Continuum, sandbox, no dead Continuum writes, no burst of failed agent sessions
  - disk space on /
Alerts go to OPS_ALERT_PHONE on WhatsApp (and Telegram when configured) once per incident, again every 6 hours
while it lasts, and once when it recovers. State lives in /var/lib/smara-watchdog (or $WATCHDOG_STATE).
"""
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ENV_FILE = Path(os.environ.get("SMARA_ENV", "/home/ubuntu/smara/.env"))
STATE = Path(os.environ.get("WATCHDOG_STATE", "/var/lib/smara-watchdog/state.json"))
PUBLIC = os.environ.get("SMARA_PUBLIC", "https://ai.syntarus.com/smara")
LOCAL = os.environ.get("SMARA_LOCAL", "http://127.0.0.1:8090")
REALERT_S = 6 * 3600
DISK_MAX_PCT = 85
FAILED_SESSIONS_MAX = 5


def read_env(path: Path = ENV_FILE) -> dict:
    env = {}
    try:
        for line in path.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    except OSError:
        pass
    return env


def http(url: str, timeout: float = 10) -> tuple[int, str]:
    """(status, body). Redirects are NOT followed: a 302 from the proxy is a failure we want to see."""
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None
    opener = urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(url, timeout=timeout) as r:
            return r.status, r.read(200_000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read(2000).decode("utf-8", "replace")
    except Exception as e:      # DNS, TLS, refused, timeout
        return 0, f"{type(e).__name__}: {e}"


def run_checks(fetch=http, disk=shutil.disk_usage) -> dict[str, str]:
    """name -> problem text, only for things that are wrong."""
    bad: dict[str, str] = {}
    code, body = fetch(f"{PUBLIC}/webhook?hub.mode=subscribe&hub.verify_token=watchdog&hub.challenge=1")
    if code != 403:
        bad["public_webhook"] = f"public webhook route answered {code} instead of 403 ({body[:80].strip()})"
    code, body = fetch(f"{LOCAL}/healthz")
    try:
        h = json.loads(body) if code == 200 else {}
    except ValueError:
        h = {}
    if code != 200 or not h.get("ok"):
        bad["app_health"] = f"/healthz answered {code}"
    else:
        if not h.get("db"):
            bad["database"] = "database check failed"
        cont = h.get("continuum") or {}
        if cont and not cont.get("ok"):
            bad["continuum"] = "memory service (Continuum) is not answering"
        if (h.get("outbox") or {}).get("dead"):
            bad["outbox_dead"] = f"{h['outbox']['dead']} memory writes gave up (dead letters)"
        if (h.get("ops") or {}).get("failed_sessions_1h", 0) >= FAILED_SESSIONS_MAX:
            bad["failed_sessions"] = f"{h['ops']['failed_sessions_1h']} agent sessions failed in the last hour"
        if h.get("sandbox") is False:
            bad["sandbox"] = "the file/Python sandbox is down"
    try:
        u = disk("/")
        pct = round(100 * u.used / u.total)
        if pct >= DISK_MAX_PCT:
            bad["disk"] = f"disk is {pct}% full"
    except OSError:
        pass
    return bad


def decide(prev: dict, bad: dict[str, str], now: float) -> tuple[list[str], dict]:
    """(messages to send, new state). Alert on a new problem, repeat every 6 h, say once when it clears."""
    msgs: list[str] = []
    since = dict(prev.get("since", {}))
    last = dict(prev.get("last_alert", {}))
    for name, text in bad.items():
        since.setdefault(name, now)
        if name not in last or now - last[name] >= REALERT_S:
            mins = int((now - since[name]) / 60)
            msgs.append(f"Smara ALERT: {text}" + (f" (for {mins} min)" if mins >= 2 else ""))
            last[name] = now
    for name in list(since):
        if name not in bad:
            msgs.append(f"Smara recovered: {name.replace('_', ' ')} is fine again")
            since.pop(name, None)
            last.pop(name, None)
    return msgs, {"since": since, "last_alert": last}


def send(env: dict, text: str) -> None:
    sent = False
    phone, token, pid = env.get("OPS_ALERT_PHONE", ""), env.get("WA_ACCESS_TOKEN", ""), env.get("WA_PHONE_NUMBER_ID", "")
    if phone and token and pid:
        req = urllib.request.Request(
            f"https://graph.facebook.com/v25.0/{pid}/messages", method="POST",
            data=json.dumps({"messaging_product": "whatsapp", "to": phone, "type": "text", "text": {"body": text[:1000]}}).encode(),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=15).read()
            sent = True
        except Exception as e:      # outside the 24h window WhatsApp refuses free text; the log line below still records it
            print(f"whatsapp alert failed: {e}", file=sys.stderr)
    tg, chat = env.get("TELEGRAM_BOT_TOKEN", ""), env.get("OPS_TELEGRAM_CHAT_ID", "")
    if tg and chat:
        try:
            urllib.request.urlopen(urllib.request.Request(
                f"https://api.telegram.org/bot{tg}/sendMessage", method="POST",
                data=urllib.parse.urlencode({"chat_id": chat, "text": text[:1000]}).encode()), timeout=15).read()
            sent = True
        except Exception as e:
            print(f"telegram alert failed: {e}", file=sys.stderr)
    print(f"{time.strftime('%FT%T')} {'sent' if sent else 'NOT DELIVERED'}: {text}")


def main() -> int:
    env, now = read_env(), time.time()
    try:
        prev = json.loads(STATE.read_text())
    except (OSError, ValueError):
        prev = {}
    msgs, new = decide(prev, run_checks(), now)
    for m in msgs:
        send(env, m)
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(new))
    except OSError as e:
        print(f"cannot save state: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
