"""One uptime check for the workflow in .github/workflows/uptime-monitor.yml.

Healthy = HTTP 200 and JSON body with "ok": true (the Smara /healthz contract).
State lives in ./state/<mode>.json (restored by actions/cache between runs).
Alerts: 2 consecutive failures -> one DOWN message; first success after an alert -> one back-up message.
Secrets are read from the environment and never printed. Exit code is always 0 so a down server does not
make the scheduled workflow red every 10 minutes (the Telegram message is the alert).
"""
import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

FAIL_THRESHOLD = 2
TIMEOUT_S = 20


def check(url):
    """Return (ok, detail)."""
    req = urllib.request.Request(url, headers={"User-Agent": "smara-uptime-monitor/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            code = resp.status
            body = resp.read(65536).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except Exception as e:  # DNS, TLS, timeout, connection refused
        return False, f"no response ({type(e).__name__})"
    if code != 200:
        return False, f"HTTP {code}"
    try:
        data = json.loads(body)
    except ValueError:
        return False, "HTTP 200 but body is not JSON"
    if data.get("ok") is not True:
        return False, "HTTP 200 but ok is not true"
    return True, "HTTP 200 ok"


def send_telegram(text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat = os.environ.get("OPS_TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        print("telegram secrets missing; alert not sent")
        return False
    data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=TIMEOUT_S) as r:
            return r.status == 200
    except Exception as e:
        print(f"telegram send failed ({type(e).__name__})")  # no URL in the message: it contains the token
        return False


def main():
    mode = os.environ["MODE"]
    target = os.environ["TARGET_URL"]
    tag = " [TEST]" if mode == "test" else ""
    state_path = pathlib.Path("state") / f"{mode}.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state = {"failures": 0, "alerted": False}
    if state_path.exists():
        try:
            state.update(json.loads(state_path.read_text()))
        except ValueError:
            pass

    ok, detail = check(target)
    now = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    print(f"{now} mode={mode} url={target} result={'UP' if ok else 'DOWN'} ({detail})")

    if ok:
        if state["alerted"]:
            send_telegram(f"Smara is back up{tag}. {target} is healthy again ({now}).")
            state["alerted"] = False
        state["failures"] = 0
    else:
        state["failures"] += 1
        if state["failures"] >= FAIL_THRESHOLD and not state["alerted"]:
            sent = send_telegram(
                f"Smara is DOWN{tag}: {target} failed {state['failures']} checks in a row ({detail}). Time: {now}."
            )
            if sent:
                state["alerted"] = True
            else:
                print("alert not sent; will retry on the next run")

    state_path.write_text(json.dumps(state))
    return 0


if __name__ == "__main__":
    sys.exit(main())
