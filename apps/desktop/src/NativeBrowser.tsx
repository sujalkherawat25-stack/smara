import { useCallback, useEffect, useRef, useState } from "react";
import { bridgeCheck, bridgeEnsure, bridgeInstallComponents, bridgePair, bridgePause, bridgeState, bridgeStop, bridgeUnpair } from "./browserBridgeApi";
import { connection, connectionText, friendlyLog, missingComponents, missingText, parseLog, shouldEnsure, validatePairInput, type BridgeState, type Components } from "./browserBridgeView";

/** Smara's browser helper runs by itself in the background. This panel only shows its state and offers pairing, stop and repair. */
export default function NativeBrowser({ locked }: { locked: boolean }) {
  const [state, setState] = useState<BridgeState | null>(null);
  const [parts, setParts] = useState<Components | null>(null);
  const [server, setServer] = useState("");
  const [code, setCode] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [pending, setPending] = useState("");
  const [confirmUnpair, setConfirmUnpair] = useState(false);
  const lastTried = useRef(0);

  const refresh = useCallback(async () => {
    const [value, components] = await Promise.all([bridgeState(), bridgeCheck()]);
    setState(value); setParts(components); setServer(current => current || value.server || value.default_server);
    return { value, components };
  }, []);
  const run = async (label: string, work: () => Promise<unknown>, done = "") => {
    setPending(label); setError(""); setNotice("");
    try { await work(); await refresh(); if (done) setNotice(done); }
    catch (reason) { setError(String(reason)); }
    finally { setPending(""); }
  };

  // Load, then make sure the helper is running (the same call Desktop makes at launch), and keep the log fresh.
  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const { value, components } = await refresh();
        if (alive && shouldEnsure(value, missingComponents(components), lastTried.current, Date.now())) {
          lastTried.current = Date.now();
          setState(await bridgeEnsure());
        }
      } catch (reason) { if (alive) setError(String(reason)); }
    };
    void tick();
    const timer = setInterval(() => { void bridgeState().then(value => { if (alive) setState(value); }).catch(() => undefined); }, 5000);
    return () => { alive = false; clearInterval(timer); };
  }, [refresh]);

  if (!state || !parts) return <section className="settings-card"><h2>Browser</h2>{error ? <p role="alert" className="native-error">{error}</p> : <p>Checking this PC…</p>}</section>;

  const status = connection(state);
  const missing = missingComponents(parts);
  const rows = parseLog(state.log);
  const busy = locked || !!pending;

  return <div className="settings-grid browser-grid">
    <section className="settings-card browser-control">
      <h2>Your own browser (your logins)</h2>
      <p>Smara uses your own browser on this PC — your home connection and your logins. It runs quietly in the background and starts with Windows, so you never need to open a terminal. Passwords, OTPs and card fields are never typed, and every page Smara works on shows a blue banner with a Stop button.</p>
      {error && <p role="alert" className="native-error">{error}</p>}
      {notice && <p role="status" className="settings-notice">{notice}</p>}
      {missing.length > 0 && <div className="native-warning" role="alert">
        <p>{missingText(missing)}</p>
        {(missing.includes("modules") && !missing.includes("python")) && <button className="primary" disabled={busy} onClick={() => void run("install", bridgeInstallComponents, "Browser components installed.")}>{pending === "install" ? "Installing…" : "Install browser components"}</button>}
        <button disabled={busy} onClick={() => void run("check", async () => undefined)}>Check again</button>
      </div>}

      {!state.paired ? <form className="settings-form" onSubmit={event => {
        event.preventDefault();
        const problem = validatePairInput(server, code);
        if (problem) { setError(problem); return; }
        void run("pair", async () => { await bridgePair(server, code); setCode(""); }, "Paired. Smara's browser helper is on and will start with Windows.");
      }}>
        <label>Server<input disabled={busy} value={server} onChange={event => setServer(event.target.value)} /></label>
        <label>Pairing code<input disabled={busy} autoComplete="off" placeholder="ABCD-EFGH" value={code} onChange={event => setCode(event.target.value)} /></label>
        <small>In WhatsApp or Telegram, send <strong>pair browser</strong> to Smara and paste the code here. The pairing is kept in <code>~/.smara-bridge/config.json</code>, which the helper reads.</small>
        <button className="primary" disabled={busy || !code.trim() || missing.includes("python") || missing.includes("modules")}>{pending === "pair" ? "Pairing…" : "Pair this PC"}</button>
      </form> : <>
        <div className={`browser-status browser-${status}`} role="status">
          <span className="browser-light" aria-hidden="true" />
          <div><strong>{connectionText(status)}</strong><small>{state.shop || "Your shop"} · {state.server}{state.startup ? " · starts with Windows" : ""}</small></div>
        </div>
        <div className="browser-big-controls">
          {state.running
            ? <><button className={state.paused ? "primary" : "browser-pause"} disabled={busy} onClick={() => void run("pause", () => bridgePause(!state.paused))}>{state.paused ? "Resume Smara" : "Pause Smara"}</button>
              <button className="danger-action" disabled={busy} onClick={() => void run("stop", bridgeStop, "Stopped. Smara cannot use your browser until you turn it on again.")}>Stop</button></>
            : <button className="primary" disabled={busy || missing.length > 0} onClick={() => void run("start", bridgeEnsure, "Turned on.")}>Turn on</button>}
        </div>
        <small>Pause refuses every step at once; the blue banner on each page also has a Stop button for that page.</small>
        <div className="telegram-pairing-section">
          <h3>This PC</h3>
          {!confirmUnpair
            ? <button className="danger-action" disabled={busy} onClick={() => setConfirmUnpair(true)}>Unpair this PC</button>
            : <div className="settings-actions"><span>Stop the helper and forget the pairing on this PC? Also send <strong>unpair browser</strong> in chat to revoke it on the server.</span>
              <button className="danger-action" disabled={busy} onClick={() => void run("unpair", async () => { setConfirmUnpair(false); await bridgeUnpair(); }, "Unpaired and switched off.")}>Yes, unpair</button>
              <button onClick={() => setConfirmUnpair(false)}>Cancel</button></div>}
        </div>
      </>}
    </section>

    <section className="settings-card browser-feed-card">
      <h2>Activity</h2>
      <p><small>Clicks and typing are approved in WhatsApp or Telegram. Pre-approved sites last 15 minutes; anything that pays, sends, submits, deletes or logs in always asks again.</small></p>
      {!rows.length ? <p>Nothing yet. What the helper does appears here.</p>
        : <ol className="browser-feed" aria-live="polite">{rows.map(row => <li key={row.id} className={`browser-step browser-step-${row.level === "error" ? "error" : "ok"}`}>
          <span className="browser-step-state">{row.level === "error" ? "Problem" : "Done"}</span>
          <div><strong>{friendlyLog(row.message)}</strong>{row.time && <small>{row.time}</small>}</div>
        </li>)}</ol>}
    </section>
  </div>;
}
