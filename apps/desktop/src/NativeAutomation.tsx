import { useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
type Browser = { options: { visible: boolean; transfers: boolean; max_actions: number; seconds: number }; sessions: { id: string; paused: boolean; stopped: boolean; alive?: boolean }[] };
type Computer = { selected: { target: { title: string }; expires: number } | null; targets: { id: string; title: string; exe: string; app?: string }[]; detail: string;
  control?: { paused: boolean }; apps?: string[]; known_apps?: Record<string, string>; hotkey?: string };
const SUGGESTED_APPS = ["excel.exe", "tallyprime.exe", "tally.exe", "busywin.exe", "explorer.exe", "notepad.exe", "calc.exe", "winword.exe"];
type OwnerBrowser = { use: boolean; browser: string; installed: string[]; connected: boolean; connected_browser: string; extension_folder: string };
const BROWSER_NAMES: Record<string, string> = { brave: "Brave", chrome: "Chrome", edge: "Edge" };

/** Smara in the owner's own Brave, Chrome or Edge (their sign-ins), through the Smara extension added once. */
export function OwnerBrowserCard({ locked }: { locked: boolean }) {
  const [state, setState] = useState<OwnerBrowser | null>(null), [choice, setChoice] = useState(""), [pending, setPending] = useState(false), [error, setError] = useState("");
  const call = async (action: string, values: Record<string, unknown> = {}) => {
    setPending(true); setError("");
    try {
      const value = await invoke<OwnerBrowser>("native_manage", { request: { operation: "owner_browser", action, ...values } });
      setState(value); setChoice(current => current || value.browser || value.installed[0] || "");
    } catch (reason) { setError(String(reason)); } finally { setPending(false); }
  };
  useEffect(() => { void call("status"); const timer = window.setInterval(() => { if (document.visibilityState !== "hidden") void call("status"); }, 10000); return () => window.clearInterval(timer); }, []);
  if (!state) return error ? <p role="alert">{error}</p> : null;
  const name = BROWSER_NAMES[choice] ?? choice;
  if (!state.installed.length) return <p>Smara can also work in your own Brave, Chrome or Edge, but none is installed on this PC.</p>;
  return <article className="owner-browser">
    <h3>Use my own browser</h3>
    <p>Smara works in your own browser, with your sign-ins, so you never sign in twice. It opens its own tabs there (a purple "Smara" group) that you can watch, and never reads your other tabs. Every click or form still asks you first.</p>
    {error && <p role="alert">{error}</p>}
    <label>Browser <select disabled={locked || pending} value={choice} onChange={event => setChoice(event.target.value)}>{state.installed.map(item => <option key={item} value={item}>{BROWSER_NAMES[item] ?? item}</option>)}</select></label>
    <p className={state.connected ? "owner-browser-on" : "owner-browser-off"}>{state.connected ? `Connected: the Smara extension is running in ${name}.` : `The Smara extension is not running in ${name} yet.`}</p>
    {!state.connected && <ol className="owner-browser-steps">
      <li><button disabled={pending} onClick={() => void call("open_extensions", { browser: choice })}>Open {name}'s extensions page</button></li>
      <li>Turn on <b>Developer mode</b> (top right).</li>
      <li>Click <b>Load unpacked</b> and choose this folder: <code>{state.extension_folder}</code> <button disabled={pending} onClick={() => void call("open_folder")}>Show folder</button></li>
    </ol>}
    <button disabled={locked || pending || !choice} onClick={() => void call("save", { use: !state.use || state.browser !== choice, browser: choice })}>
      {state.use && state.browser === choice ? `Stop using ${name}` : `Use ${name} for Smara`}</button>
    <small>{state.use ? `Smara uses ${BROWSER_NAMES[state.browser] ?? state.browser} from the next chat.` : "Smara uses its own browser."} If {name} is closed, Smara opens it.</small>
  </article>;
}

export default function NativeAutomation({ workspace, locked, onPendingChange, onSelectionChange }: { workspace: string; locked: boolean; onPendingChange: (value: boolean) => void; onSelectionChange?: () => Promise<void> }) {
  const busy = useRef(false);
  const [browser, setBrowser] = useState<Browser | null>(null), [computer, setComputer] = useState<Computer | null>(null), [error, setError] = useState(""), [pending, setPending] = useState(false);
  const [apps, setApps] = useState<string[] | null>(null);
  const request = async (operation: "browser_control" | "computer_control", action = "status", values: Record<string, unknown> = {}) => {
    if (busy.current) return; busy.current = true; setPending(true); onPendingChange(true); setError("");
    try {
      const result = await invoke<Browser | Computer>("native_manage", { request: { operation, action, workspace, ...values } });
      if (operation === "browser_control") { const value = result as Partial<Browser>; setBrowser({ options: value.options ?? { visible: true, transfers: false, max_actions: 80, seconds: 900 }, sessions: value.sessions ?? [] }); }
      else { const value = result as Computer; setComputer(value); if (value.apps) setApps(value.apps); if (onSelectionChange) await onSelectionChange(); }
    } catch (reason) { setError(String(reason)); } finally { busy.current = false; setPending(false); onPendingChange(false); }
  };
  useEffect(() => { void request("browser_control"); }, [workspace]);
  const disabled = locked || pending;
  return <section className="settings-card settings-form"><h2>Browser & Windows apps</h2>{error && <p role="alert">{error}</p>}
    <p>Smara's own browser is a separate window with its own sign-ins. Sites follow the website setting above.</p>
    {browser && <><label><input type="checkbox" disabled={disabled} checked={browser.options.visible} onChange={event => setBrowser({ ...browser, options: { ...browser.options, visible: event.target.checked } })} /> Show Smara's browser window while it works</label>
      <label><input type="checkbox" disabled={disabled} checked={browser.options.transfers} onChange={event => setBrowser({ ...browser, options: { ...browser.options, transfers: event.target.checked } })} /> Allow reviewed file transfers</label>
      <small>Transfers still ask for native approval. Uploads may transmit immediately. Accepted files are at most 10 MB; executables and hidden files are blocked. Retrieval size is checked after browser download, not a network byte quota.</small>
      <label>Browser action limit<input disabled={disabled} type="number" min={1} max={500} value={browser.options.max_actions} onChange={event => setBrowser({ ...browser, options: { ...browser.options, max_actions: Number(event.target.value) } })} /></label>
      <label>Browser seconds<input disabled={disabled} type="number" min={60} max={3600} value={browser.options.seconds} onChange={event => setBrowser({ ...browser, options: { ...browser.options, seconds: Number(event.target.value) } })} /></label>
      <button disabled={disabled} onClick={() => { if (window.confirm("Save these owned-browser choices? Every model action still requires native confirmation. Changes apply to the next session.")) void request("browser_control", "save", { options: browser.options }); }}>Save browser choices</button>
      <button disabled={pending} onClick={() => void request("browser_control")}>Refresh browser sessions</button>
      {browser.sessions.map(item => <article key={item.id}><small>Owned session {item.id.slice(0, 8)} · {item.alive === false ? "Helper exited — reconnect" : item.paused ? "Human handoff" : item.stopped ? "Stop requested" : "Available"}</small>
        {item.paused && <button disabled={pending || item.alive === false} onClick={() => { if (window.confirm("Resume after your login? Page text or screenshots you approve can be sent to the selected model. This uses only the owned browser, not personal cookies.")) void request("browser_control", "resume", { id: item.id }); }}>Resume owned browser</button>}
        <button disabled={pending} onClick={() => void request("browser_control", "stop", { id: item.id })}>Stop owned browser</button></article>)}</>}
    <h3>Windows app control</h3><p>Off by default. Allow the apps you tick below, or one selected window, for 30 minutes (up to 400 actions). Smara brings the app forward itself and uses real pointer and key events. It never drives terminals, security/login dialogs or password managers, never types passwords, OTPs or card numbers, and asks you first for send, submit, pay, delete and similar steps.</p>
    <p>While Smara may control your apps, a bar at the top of the screen says so, with Pause and Stop. {computer?.hotkey ?? "Ctrl+Alt+Esc"} also stops it at once. Every step and its screenshot go to Settings &gt; Activity log.</p>
    <button disabled={pending} onClick={() => void request("computer_control")}>Refresh eligible app windows</button>
    {computer && <><small>{computer.detail}</small><p>{computer.selected ? `Allowed now: ${computer.selected.target.title}` : "No app allowed"}{computer.control?.paused ? " · Paused" : ""}</p>
      <button disabled={pending} onClick={() => void request("computer_control", "stop")}>Stop computer control</button>
      {computer.control && <button disabled={pending} onClick={() => void request("computer_control", computer.control?.paused ? "resume" : "pause")}>{computer.control.paused ? "Resume app control" : "Pause app control"}</button>}
      {apps && <fieldset className="app-allow-list"><legend>Apps Smara may control</legend>
        {[...new Set([...SUGGESTED_APPS, ...apps, ...(computer.targets ?? []).map(item => (item.app ?? item.exe.split(/[\\/]/).pop() ?? "").toLowerCase()).filter(Boolean)])].map(app =>
          <label key={app}><input type="checkbox" disabled={disabled} checked={apps.includes(app)} onChange={event => setApps(event.target.checked ? [...apps, app] : apps.filter(item => item !== app))} /> {computer.known_apps?.[app] ?? app}</label>)}
        <button disabled={disabled || apps.length === 0} onClick={() => void request("computer_control", "save_apps", { apps })}>Save allowed apps</button>
      </fieldset>}
      <button disabled={disabled || pending || (apps !== null && apps.length === 0)} onClick={() => { if (window.confirm("Let Smara control your allowed apps for 30 minutes? Approved observations may send on-screen text to your model. Routine clicks and typing run without asking; send, submit, pay and delete steps still ask you. Pause or Stop any time on the Smara bar.")) void request("computer_control", "grant_desktop", { minutes: 30 }); }}>Allow my apps for 30 minutes</button>
      <button disabled={disabled || pending} onClick={() => { if (window.confirm("Let Smara control ANY app window (not only your allowed apps) for 30 minutes? Terminals, security dialogs and password managers stay blocked. Only do this when a task needs an app you have not listed.")) void request("computer_control", "grant_desktop", { minutes: 30, any_app: true }); }}>Any app for 30 minutes</button>
      {(computer.targets ?? []).map(item => <button key={item.id} disabled={disabled} onClick={() => { if (window.confirm(`Select ${item.title}? Approved observations may send this app's text to your model. Screenshots stay local unless separately viewed/analyzed. Critical steps still ask you. Return to chat to use the new tools.`)) void request("computer_control", "select", { id: item.id }); }}>{item.title || item.exe}</button>)}</>}
    <small>These app tools are not a Windows sandbox or certification of general computer-use safety. Never claim visual review unless a vision-capable model actually reviewed the image.</small>
  </section>;
}
