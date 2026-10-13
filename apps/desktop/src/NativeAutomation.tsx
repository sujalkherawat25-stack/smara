import { useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
type Browser = { options: { visible: boolean; transfers: boolean; max_actions: number; seconds: number }; sessions: { id: string; paused: boolean; stopped: boolean; alive?: boolean }[] };
type Computer = { selected: { target: { title: string }; expires: number } | null; targets: { id: string; title: string; exe: string }[]; detail: string };
export default function NativeAutomation({ workspace, locked, onPendingChange, onSelectionChange }: { workspace: string; locked: boolean; onPendingChange: (value: boolean) => void; onSelectionChange?: () => Promise<void> }) {
  const busy = useRef(false);
  const [browser, setBrowser] = useState<Browser | null>(null), [computer, setComputer] = useState<Computer | null>(null), [error, setError] = useState(""), [pending, setPending] = useState(false);
  const request = async (operation: "browser_control" | "computer_control", action = "status", values: Record<string, unknown> = {}) => {
    if (busy.current) return; busy.current = true; setPending(true); onPendingChange(true); setError("");
    try {
      const result = await invoke<Browser | Computer>("native_manage", { request: { operation, action, workspace, ...values } });
      if (operation === "browser_control") setBrowser(result as Browser); else { setComputer(result as Computer); if (onSelectionChange) await onSelectionChange(); }
    } catch (reason) { setError(String(reason)); } finally { busy.current = false; setPending(false); onPendingChange(false); }
  };
  useEffect(() => { void request("browser_control"); }, [workspace]);
  const disabled = locked || pending;
  return <section className="settings-card settings-form"><h2>Browser & computer</h2>{error && <p role="alert">{error}</p>}
    <p>Use fresh owned browser sessions, never your personal browser profile. Sites must also be listed in Approved public browser origins above.</p>
    {browser && <><label><input type="checkbox" disabled={disabled} checked={browser.options.visible} onChange={event => setBrowser({ ...browser, options: { ...browser.options, visible: event.target.checked } })} /> Show owned browser</label>
      <label><input type="checkbox" disabled={disabled} checked={browser.options.transfers} onChange={event => setBrowser({ ...browser, options: { ...browser.options, transfers: event.target.checked } })} /> Allow reviewed file transfers</label>
      <small>Transfers still ask for native approval. Uploads may transmit immediately. Accepted files are at most 10 MB; executables and hidden files are blocked. Retrieval size is checked after browser download, not a network byte quota.</small>
      <label>Browser action limit<input disabled={disabled} type="number" min={1} max={500} value={browser.options.max_actions} onChange={event => setBrowser({ ...browser, options: { ...browser.options, max_actions: Number(event.target.value) } })} /></label>
      <label>Browser seconds<input disabled={disabled} type="number" min={60} max={3600} value={browser.options.seconds} onChange={event => setBrowser({ ...browser, options: { ...browser.options, seconds: Number(event.target.value) } })} /></label>
      <button disabled={disabled} onClick={() => { if (window.confirm("Save these owned-browser choices? Every model action still requires native confirmation. Changes apply to the next session.")) void request("browser_control", "save", { options: browser.options }); }}>Save browser choices</button>
      <button disabled={pending} onClick={() => void request("browser_control")}>Refresh browser sessions</button>
      {browser.sessions.map(item => <article key={item.id}><small>Owned session {item.id.slice(0, 8)} · {item.alive === false ? "Helper exited — reconnect" : item.paused ? "Human handoff" : item.stopped ? "Stop requested" : "Available"}</small>
        {item.paused && <button disabled={pending || item.alive === false} onClick={() => { if (window.confirm("Resume after your login? Page text or screenshots you approve can be sent to the selected model. This uses only the owned browser, not personal cookies.")) void request("browser_control", "resume", { id: item.id }); }}>Resume owned browser</button>}
        <button disabled={pending} onClick={() => void request("browser_control", "stop", { id: item.id })}>Stop owned browser</button></article>)}</>}
    <h3>Experimental Windows app control</h3><p>Off by default. Select one live Notepad, Paint or Calculator window for 15 minutes / 40 actions. After each native approval, bring that app to the foreground within 10 seconds. Smara never switches focus for you. No terminals, login/security dialogs, global keyboard shortcuts or unrestricted coordinates.</p>
    <button disabled={pending} onClick={() => void request("computer_control")}>Refresh eligible app windows</button>
    {computer && <><small>{computer.detail}</small><p>{computer.selected ? `Selected: ${computer.selected.target.title}` : "No app selected"}</p>
      <button disabled={pending} onClick={() => void request("computer_control", "stop")}>Stop computer control</button>
      {computer.targets.map(item => <button key={item.id} disabled={disabled} onClick={() => { if (window.confirm(`Select ${item.title}? Approved observations may send this app's text to your model. Screenshots stay local unless separately viewed/analyzed. Each action asks for confirmation. Return to chat to use the new tools.`)) void request("computer_control", "select", { id: item.id }); }}>{item.title || item.exe}</button>)}</>}
    <small>These app tools are not a Windows sandbox or certification of general computer-use safety. Never claim visual review unless a vision-capable model actually reviewed the image.</small>
  </section>;
}
