import { useCallback, useEffect, useRef, useState } from "react";
import { bridgeCheck, bridgePair, bridgePause, bridgeSaveOptions, bridgeStart, bridgeState, bridgeStop, bridgeUnpair, onBridgeEvent, type BridgeCheck } from "./browserBridgeApi";
import {
  APPROVAL_BUTTONS, buildFeed, connection, connectionText, describeApproval, describeBrowser, mergeEvents, pendingApprovals, settleFeed,
  shouldAutoStart, validatePairInput, type ApprovalCard, type ApprovalChoice, type BridgeConfig, type BridgeEvent, type BridgeState,
} from "./browserBridgeView";

const BROWSERS = [["brave", "Brave (recommended)"], ["chrome", "Chrome"], ["edge", "Edge"], ["auto", "First one found"]];

/** Approval cards. Chat (WhatsApp/Telegram) stays the authority; the Desktop buttons need a server endpoint that does not exist yet. */
function Approvals({ cards, onDecide }: { cards: ApprovalCard[]; onDecide: (card: ApprovalCard, choice: ApprovalChoice) => void }) {
  const [answered] = useState(() => new Set<string>());
  const visible = pendingApprovals(cards, answered, Date.now());
  if (!visible.length) return null;
  return <section className="settings-card browser-approvals" aria-label="Approvals waiting">
    <h2>Smara needs your OK</h2>
    {visible.map(card => <div className="browser-approval" key={card.id}>
      <p>{describeApproval(card)}</p>
      <div className="settings-actions">{APPROVAL_BUTTONS.map(button =>
        <button key={button.choice} className={button.choice === "approve" ? "primary" : button.choice === "reject" ? "danger-action" : ""} onClick={() => onDecide(card, button.choice)}>{button.label}</button>)}
      </div>
    </div>)}
  </section>;
}

export default function NativeBrowser({ locked }: { locked: boolean }) {
  const [state, setState] = useState<BridgeState | null>(null);
  const [events, setEvents] = useState<BridgeEvent[]>([]);
  const [now, setNow] = useState(Date.now());
  const [server, setServer] = useState("");
  const [code, setCode] = useState("");
  const [options, setOptions] = useState<BridgeConfig | null>(null);
  const [check, setCheck] = useState<BridgeCheck | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [pending, setPending] = useState(false);
  const [confirmUnpair, setConfirmUnpair] = useState(false);
  const autoTried = useRef(false);

  const apply = useCallback((value: BridgeState) => {
    setState(value); setEvents(value.events);
    setOptions(current => current ?? value.config);
    setServer(current => current || value.config.server || value.default_server);
  }, []);
  const refresh = useCallback(async () => { const value = await bridgeState(); setState(value); setOptions(current => current ?? value.config); return value; }, []);
  const run = async (work: () => Promise<BridgeState | void>, done = "") => {
    setPending(true); setError(""); setNotice("");
    try { const value = await work(); if (value) { setState(value); setOptions(value.config); } if (done) setNotice(done); }
    catch (reason) { setError(String(reason)); }
    finally { setPending(false); }
  };

  useEffect(() => {
    let alive = true; let unlisten: (() => void) | undefined;
    void bridgeState().then(value => { if (alive) apply(value); }).catch(reason => setError(String(reason)));
    void bridgeCheck().then(value => { if (alive) setCheck(value); }).catch(reason => { if (alive) setError(String(reason)); });
    void onBridgeEvent(event => {
      setEvents(current => mergeEvents(current, event));
      setState(current => current && event.kind === "poll" && event.ok ? { ...current, last_poll_ok_ms: event.ts } : current);
      if (["started", "paused", "fatal", "exited", "stopped"].includes(event.kind)) void refresh();
    }).then(stop => { if (alive) unlisten = stop; else stop(); });
    const timer = setInterval(() => setNow(Date.now()), 5000);
    return () => { alive = false; unlisten?.(); clearInterval(timer); };
  }, [apply, refresh]);

  useEffect(() => {
    if (state && shouldAutoStart(state, pending, autoTried.current)) {
      autoTried.current = true;
      void run(bridgeStart);
    }
  }, [state]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!state || !options) return <section className="settings-card"><h2>Browser</h2>{error ? <p role="alert" className="native-error">{error}</p> : <p>Loading…</p>}</section>;

  const status = connection(state, now);
  const feed = settleFeed(buildFeed(events), state.running).slice().reverse();
  const busy = locked || pending;
  const ready = check ? check.python && check.playwright : true;
  const setOption = (patch: Partial<BridgeConfig>) => setOptions({ ...options, ...patch });
  const optionsDirty = JSON.stringify([options.browser, options.attach, options.profile, options.autostart]) !== JSON.stringify([state.config.browser, state.config.attach, state.config.profile, state.config.autostart]);

  return <div className="settings-grid browser-grid">
    <section className="settings-card browser-control">
      <h2>Smara Browser</h2>
      <p>Lets Smara use your own browser on this PC — your home connection and your logins. Passwords, OTPs and card fields are never typed, and every page Smara works on shows a blue banner with a Stop button.</p>
      {error && <p role="alert" className="native-error">{error}</p>}
      {notice && <p role="status" className="settings-notice">{notice}</p>}
      {check && !ready && <p role="alert" className="native-warning">{!check.python ? "Python 3 was not found." : "Playwright is not installed."} Install with: <code>pip install playwright httpx</code></p>}

      {!state.paired ? <form className="settings-form" onSubmit={event => {
        event.preventDefault();
        const problem = validatePairInput(server, code);
        if (problem) { setError(problem); return; }
        void run(async () => { const value = await bridgePair(server, code); setCode(""); return value; }, "Paired. You can start the browser now.");
      }}>
        <label>Server<input disabled={busy} value={server} onChange={event => setServer(event.target.value)} /></label>
        <label>Pairing code<input disabled={busy} autoComplete="off" placeholder="ABCD-EFGH" value={code} onChange={event => setCode(event.target.value)} /></label>
        <small>In WhatsApp or Telegram, send <strong>pair browser</strong> to Smara and paste the code here. The device key is kept in the Windows credential store, not in a file.</small>
        <button className="primary" disabled={busy || !code.trim()}>Pair this PC</button>
      </form> : <>
        <div className={`browser-status browser-${status}`} role="status">
          <span className="browser-light" aria-hidden="true" />
          <div><strong>{connectionText(status)}</strong><small>{state.config.shop} · {state.config.server}</small></div>
        </div>
        <div className="browser-big-controls">
          {!state.running
            ? <button className="primary" disabled={busy || !ready} onClick={() => void run(bridgeStart)}>Start browser</button>
            : <>
              <button className={state.paused ? "primary" : "browser-pause"} disabled={pending} onClick={() => void run(() => bridgePause(!state.paused))}>{state.paused ? "▶ Resume Smara" : "⏸ Pause Smara"}</button>
              <button className="danger-action" disabled={pending} onClick={() => void run(bridgeStop)}>■ Stop</button>
              <button disabled={pending} onClick={() => void run(async () => { await bridgeStop(); return bridgeStart(); })}>Restart</button>
            </>}
        </div>
        {state.running && <small>Pause matches the blue banner on each page: pressing either one pauses both.</small>}
        <div className="settings-form">
          <h3>Browser</h3>
          <label>Which browser<select disabled={busy || state.running} value={options.browser} onChange={event => setOption({ browser: event.target.value })}>
            {BROWSERS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
          <label>Attach to a running browser (optional)<input disabled={busy || state.running} placeholder="http://127.0.0.1:9222" value={options.attach} onChange={event => setOption({ attach: event.target.value.trim() })} /></label>
          <small>{options.attach ? "Start Brave with --remote-debugging-port=9222 first. Smara will use that window and its logins." : `Smara opens ${describeBrowser(options)} with its own profile so your everyday browser is untouched. Log in to the sites you want it to use once.`}</small>
          <label>Profile folder<input disabled={busy || state.running || !!options.attach} placeholder={state.default_profile} value={options.profile} onChange={event => setOption({ profile: event.target.value })} /></label>
          <label className="native-tools-choice"><input type="checkbox" disabled={busy} checked={options.autostart} onChange={event => setOption({ autostart: event.target.checked })} /> Start automatically when Smara Desktop opens</label>
          <button disabled={busy || state.running || !optionsDirty} onClick={() => void run(() => bridgeSaveOptions(options), "Browser options saved.")}>Save browser options</button>
        </div>
        <div className="telegram-pairing-section">
          <h3>This PC</h3>
          {!confirmUnpair
            ? <button className="danger-action" disabled={busy} onClick={() => setConfirmUnpair(true)}>Unpair this PC</button>
            : <div className="settings-actions"><span>Forget the key on this PC? Also send <strong>unpair browser</strong> in chat to revoke it on the server.</span>
              <button className="danger-action" disabled={busy} onClick={() => void run(async () => { setConfirmUnpair(false); return bridgeUnpair(); }, "Unpaired. The device key was removed from this PC.")}>Yes, unpair</button>
              <button onClick={() => setConfirmUnpair(false)}>Cancel</button></div>}
        </div>
      </>}
    </section>

    <section className="settings-card browser-feed-card">
      <h2>Activity</h2>
      <Approvals cards={[]} onDecide={() => undefined} />
      <p><small>Approvals for clicks and typing arrive in WhatsApp or Telegram. Pre-approved sites last 15 minutes; anything that pays, sends, submits, deletes or logs in always asks again.</small></p>
      {!feed.length ? <p>{state.running ? "Waiting for Smara to use the browser…" : "Nothing yet. Steps appear here as Smara works."}</p>
        : <ol className="browser-feed" aria-live="polite">{feed.map(row => <li key={row.id} className={`browser-step browser-step-${row.status}`}>
          <span className="browser-step-icon" aria-hidden="true">{row.status === "running" ? "…" : row.status === "ok" ? "✓" : "✕"}</span>
          <div><strong>{row.label}</strong>
            <small>{[row.host, row.title, row.ms ? `${(row.ms / 1000).toFixed(1)}s` : "", new Date(row.ts).toLocaleTimeString()].filter(Boolean).join(" · ")}</small>
            {row.error && <small className="native-error">{row.error}</small>}</div>
        </li>)}</ol>}
    </section>
  </div>;
}
