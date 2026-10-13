"""User-started desktop OAuth: loopback, state, PKCE, protected tokens.

No password/token input is exposed to the model. Google consent stays in the
system browser; this process never approves that consent for the user.
"""
import base64
import hashlib
import hmac
from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import os
import secrets
import subprocess
import sys
import time
import uuid
from urllib.parse import urlencode, urlsplit, parse_qs
import webbrowser
import httpx
from .native_profiles import state_path, read_object, write_object, resolve_credential
from .native_outreach import config, API, SCOPES, TOKEN_ALIAS, CLIENT_ALIAS, address


def health_path(): return state_path().parent / "native-mail-connection.json"


def start():
    from .native_management import management_lock
    with management_lock():
        options = config()
        if not options["client_id"]: raise ValueError("Add a Google Desktop OAuth client ID in Settings → Email")
        health = read_object(health_path())
        if health.get("state") == "connecting" and health.get("updated", 0) > time.time() - 200:
            raise ValueError("A Gmail connection is already waiting in your system browser")
        generation = uuid.uuid4().hex
        write_object(health_path(), {"state": "connecting", "generation": generation, "updated": time.time()})
        args = ([sys.executable, "--native-mail-connect"] if getattr(sys, "frozen", False)
                else [sys.executable, "-m", "smara.native_mail_oauth"])
        args += ["--generation", generation]
        try:
            subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0, start_new_session=os.name != "nt")
        except OSError:
            write_object(health_path(), {"state": "failed", "updated": time.time()})
            raise ValueError("Could not start the Gmail connection helper") from None
    return {"state": "connecting", "detail": "Finish Google's consent in the system browser; no email will be sent"}


def authorize(*, timeout=180, client=None, opener=webbrowser.open, generation=None):
    options = config()
    verifier, nonce = secrets.token_urlsafe(48), secrets.token_urlsafe(32)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    captured = {}
    class Callback(BaseHTTPRequestHandler):
        def log_message(self, *_args): pass
        def do_GET(self):
            parsed = urlsplit(self.path); query = parse_qs(parsed.query)
            valid = (parsed.path == "/callback" and self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"
                     and len(query.get("state", [])) == 1 and hmac.compare_digest(query["state"][0], nonce))
            if valid:
                if len(query.get("code", [])) == 1 and len(query["code"][0]) < 4000:
                    captured["code"] = query["code"][0]
                else: captured["denied"] = True
            body = b"Return to Smara. No email has been sent." if valid else b"Invalid authorization callback."
            self.send_response(200 if valid else 400)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    class BoundedServer(HTTPServer):
        def get_request(self):
            connection, peer = super().get_request()
            connection.settimeout(2)
            return connection, peer
    server = BoundedServer(("127.0.0.1", 0), Callback)
    server.timeout = .5
    redirect = f"http://127.0.0.1:{server.server_port}/callback"
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({"client_id": options["client_id"],
        "redirect_uri": redirect, "response_type": "code", "scope": " ".join(sorted(SCOPES)),
        "state": nonce, "code_challenge": challenge, "code_challenge_method": "S256", "access_type": "offline", "prompt": "consent"})
    owns = client is None
    client = client or httpx.Client(timeout=20, trust_env=False, follow_redirects=False)
    try:
        if read_object(health_path()).get("generation") != generation:
            raise ValueError("Connection was cancelled before authorization began")
        if not options["client_id"] or not opener(url): raise ValueError("Could not open Google authorization")
        deadline = time.monotonic() + timeout
        while not captured and time.monotonic() < deadline: server.handle_request()
        if not captured.get("code"): raise ValueError("Google authorization was denied or timed out")
        response = client.post("https://oauth2.googleapis.com/token", data={"code": captured["code"],
            "client_id": options["client_id"], "client_secret": resolve_credential(CLIENT_ALIAS, allow_environment=False),
            "redirect_uri": redirect, "grant_type": "authorization_code", "code_verifier": verifier})
        response.raise_for_status(); token = response.json()
        if not token.get("access_token") or not token.get("refresh_token") or not SCOPES.issubset(set(token.get("scope", "").split())):
            raise ValueError("Google did not grant the requested durable sending/read-back access")
        response = client.get(API + "/profile", headers={"Authorization": "Bearer " + token["access_token"]})
        response.raise_for_status()
        token["email"] = address(response.json()["emailAddress"])
        token["expires_at"] = time.time() + int(token.get("expires_in", 3600))
        from .native_management import management_lock, put_credential
        with management_lock():
            # Do not attach an old authorization to a newly selected OAuth client.
            if config()["client_id"] != options["client_id"]: raise ValueError("OAuth client changed during connection; reconnect")
            if read_object(health_path()).get("generation") != generation:
                raise ValueError("Connection was cancelled or replaced; reconnect explicitly")
            put_credential(TOKEN_ALIAS, json.dumps(token), "gmail")
            write_object(health_path(), {"state": "connected", "generation": generation, "updated": time.time()})
        return {"connected": True, "sender": token["email"]}
    except Exception:
        from .native_management import management_lock
        with management_lock():
            if read_object(health_path()).get("generation") == generation:
                write_object(health_path(), {"state": "failed", "generation": generation, "updated": time.time(), "detail": "Authorization failed, declined or expired. No email sent."})
        raise
    finally:
        server.server_close()
        if owns: client.close()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--generation")
    try: authorize(generation=parser.parse_args().generation)
    except Exception: raise SystemExit(1)
