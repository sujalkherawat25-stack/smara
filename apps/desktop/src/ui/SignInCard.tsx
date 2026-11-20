import { useCallback, useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { bridgeEnsure, bridgeStop } from "../browserBridgeApi";
import { EMPTY_SIGN_IN, connectedLine, finished, minutesLeft, waitingText, type SignIn } from "../signIn";

const login = (action: string) => invoke<SignIn>("native_manage", { request: { operation: "hosted_login", action } });
const POLL_MS = 3000;

/** One place to connect this PC to Smara: Sign in, tap a link in your own chat, say YES. No codes to paste. */
export default function SignInCard({ helperLabel, onChange, locked }: { helperLabel: string; onChange: () => void; locked: boolean }) {
  const [state, setState] = useState<SignIn>(EMPTY_SIGN_IN);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirmOut, setConfirmOut] = useState(false);
  const alive = useRef(true);

  const refresh = useCallback(async () => {
    try { const value = await login("status"); if (alive.current) setState(value); } catch (reason) { if (alive.current) setError(String(reason)); }
  }, []);
  useEffect(() => { alive.current = true; void refresh(); return () => { alive.current = false; }; }, [refresh]);

  // While a sign-in is waiting, ask the server every few seconds whether the owner said YES.
  useEffect(() => {
    if (!state.waiting) return;
    const timer = window.setInterval(async () => {
      try {
        const next = await login("poll");
        if (!alive.current) return;
        setState(next);
        if (next.state === "approved") { await bridgeEnsure().catch(() => undefined); onChange(); }
      } catch (reason) { if (alive.current) setError(String(reason)); }
    }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [state.waiting, onChange]);

  const run = async (work: () => Promise<SignIn | void>) => {
    setBusy(true); setError("");
    try { const value = await work(); if (value && alive.current) setState(value); onChange(); } catch (reason) { setError(String(reason)); } finally { if (alive.current) setBusy(false); }
  };
  const open = (url?: string) => { if (url) void invoke("native_open_url", { url }).catch(reason => setError(String(reason))); };

  if (state.waiting) {
    const links = state.links;
    return <section className="signin-card" aria-label="Sign in to Smara">
      <h2>Confirm this sign-in</h2>
      <p className="signin-help">{waitingText(state.state)}</p>
      <div className="signin-code" aria-label="Your sign-in code">{state.code}</div>
      <small>Check this code matches in the chat. Valid for {minutesLeft(state.expires_at, Date.now() / 1000)} more minutes.</small>
      <div className="signin-actions">
        {links.telegram && <button className="primary" onClick={() => open(links.telegram)}>Open Telegram</button>}
        {links.whatsapp && <button className={links.telegram ? "" : "primary"} onClick={() => open(links.whatsapp)}>Open WhatsApp</button>}
        <button disabled={busy} onClick={() => void run(() => login("cancel"))}>Cancel</button>
      </div>
      <small>No link? Send <b>{state.message}</b> to Smara in the WhatsApp or Telegram you already use for your shop.</small>
      {error && <p role="alert" className="native-error">{error}</p>}
    </section>;
  }

  if (state.signed_in) {
    return <section className="signin-card signin-on" aria-label="Smara account">
      <div className="signin-status"><span className="status-dot" aria-hidden="true" /><div><strong>{connectedLine(state.shop, helperLabel)}</strong><small>Approvals and your own browser are connected on this PC.</small></div></div>
      {confirmOut
        ? <div className="signin-actions"><span>Sign out of Smara on this PC?</span>
            <button className="danger-action" disabled={busy || locked} onClick={() => void run(async () => { setConfirmOut(false); await bridgeStop().catch(() => undefined); return login("sign_out"); })}>Yes, sign out</button>
            <button onClick={() => setConfirmOut(false)}>Cancel</button></div>
        : <button className="text-button" disabled={busy || locked} onClick={() => setConfirmOut(true)}>Sign out</button>}
      {error && <p role="alert" className="native-error">{error}</p>}
    </section>;
  }

  return <section className="signin-card" aria-label="Sign in to Smara">
    <h2>{state.partial ? "Finish connecting this PC" : "Sign in to Smara"}</h2>
    <p className="signin-help">{state.partial ? "Part of the old pairing is still on this PC. Sign in once to connect everything." : "Connect this PC to your shop: answer Smara's steps here and let it use your own browser. You confirm in your own chat, so there is no password."}</p>
    <div className="signin-actions"><button className="primary" disabled={busy || locked} onClick={() => void run(() => login("start"))}>{busy ? "Starting…" : "Sign in"}</button></div>
    {error && <p role="alert" className="native-error">{error}</p>}
  </section>;
}
