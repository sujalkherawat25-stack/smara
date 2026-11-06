import { useEffect, useState } from "react";
import { nativeProbe, nativeSettings, type NativeSetup } from "./nativeApi";

type Props = { setup: NativeSetup; locked: boolean; onUpdate: (value: NativeSetup) => void; onPendingChange: (value: boolean) => void };

/** Syntarus is Smara's memory, and it is always on: saving a key connects and enables it in one step, and a saved key
 *  is switched back on by the app if something turned memory off. There is no switch to forget about. */
export function memoryShouldBeOn(memory?: { enabled: boolean; configured: boolean }): boolean {
  return !!memory?.configured && !memory.enabled;
}
export default function NativeMemorySettings({ setup, locked, onUpdate, onPendingChange }: Props) {
  const [secret, setSecret] = useState("");
  const [scope, setScope] = useState<"project" | "personal">(setup.memory?.scope ?? "project");
  const [identity, setIdentity] = useState(setup.memory?.user_id ?? "");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  useEffect(() => { setScope(setup.memory?.scope ?? "project"); setIdentity(setup.memory?.user_id ?? ""); }, [setup.memory?.scope, setup.memory?.user_id]);
  const disabled = locked || pending;
  const connected = !!setup.memory?.configured;
  const on = connected && !!setup.memory?.enabled;
  const run = async (action: "save" | "probe" | "remove") => {
    if (disabled) return;
    if (action === "probe" && !window.confirm("Send one fixed synthetic search query to Syntarus? No personal conversation, project files or memory writes are included. Provider usage may be charged.")) return;
    if (action === "remove" && !window.confirm("Disconnect Syntarus? The saved key is removed and memory stops. Local history and memories already stored at Syntarus are kept.")) return;
    setPending(true); onPendingChange(true); setError(""); setNotice("");
    try {
      if (action === "probe") {
        const result = await nativeProbe("test_memory");
        setNotice(result.detail ?? "Syntarus responded.");
      } else {
        const result = await nativeSettings(action === "save" ? "save_memory" : "delete_credential", action === "save" ? { secret, memory: { enabled: true, scope, user_id: identity } } : { name: "SYNTARUS_API_KEY" });
        onUpdate(result);
        setNotice(action === "save" ? "Syntarus memory is on. Smara now remembers across chats." : "Disconnected. Local history and stored memories were kept.");
      }
    } catch (reason) { setError(String(reason)); }
    finally { setSecret(""); setPending(false); onPendingChange(false); }
  };
  return <div className="set-page">
    <header><h2>Memory</h2><p>Smara uses Syntarus to remember your preferences, decisions and past chats, and recalls them before answering.</p></header>
    {error && <p role="alert" className="native-error">{error}</p>}{notice && <p role="status" className="settings-notice">{notice}</p>}
    <form className="set-group" onSubmit={event => { event.preventDefault(); void run("save"); }}>
      <div className="set-row">
        <div><strong>Syntarus memory</strong><small className={`set-state ${on ? "ok" : "attention"}`}>{on ? "On" : connected ? "Connected, starting…" : "Add your key to turn it on"}</small></div>
        {connected && <div className="set-actions"><button type="button" disabled={disabled} onClick={() => void run("probe")}>Test connection</button><button type="button" className="danger-action" disabled={disabled} onClick={() => void run("remove")}>Disconnect</button></div>}
      </div>
      <div className="set-row stack">
        <label>Syntarus project key<input aria-label="Syntarus project key" type="password" autoComplete="off" disabled={disabled} value={secret} onChange={event => setSecret(event.target.value)} placeholder={connected ? "Saved. Paste a new key to replace it" : "sk_mem_…"} /></label>
        <small>Encrypted on this PC with Windows DPAPI. The key is never shown again or sent to a model.</small>
        <div className="set-actions"><button className="primary" disabled={disabled || !secret.trim()}>{connected ? "Replace key" : "Connect Syntarus"}</button></div>
      </div>
    </form>
    <details className="set-group set-advanced"><summary>Advanced</summary>
      <div className="set-row stack"><label>Memory scope<select aria-label="Conversation memory scope" disabled={disabled} value={scope} onChange={event => setScope(event.target.value as "project" | "personal")}><option value="project">Separate memory for each project</option><option value="personal">Personal memory shared across my projects</option></select></label>
        <label>Memory user ID<input aria-label="Syntarus memory user ID" disabled={disabled} value={identity} onChange={event => setIdentity(event.target.value)} placeholder="Created automatically when you connect" /></label>
        <small>Use an existing Syntarus user ID to recall its history. Changing the ID or scope does not migrate memories or pending writes.</small>
        <div className="set-actions"><button type="button" disabled={disabled || !connected} onClick={() => void run("save")}>Save scope and ID</button></div></div>
      <div className="set-row stack"><strong>Status</strong>
        <small>{setup.memory?.detail ?? "Add a Syntarus key to get started."}</small>
        {setup.memory?.last_activity && <small role="status">{setup.memory.last_activity.detail} · {new Date(setup.memory.last_activity.updated * 1000).toLocaleString()}</small>}
        <small>Pending {(setup.memory?.writes.pending ?? 0) + (setup.memory?.writes.submitted ?? 0)} · processed {setup.memory?.writes.succeeded ?? 0} · failed {setup.memory?.writes.failed ?? 0}. A queued write is not a processed memory; Syntarus ingests asynchronously.</small></div>
      <div className="set-row stack"><strong>Skills</strong>
        <small>Reusable workflows live in <code>&lt;skill-name&gt;/SKILL.md</code> with name and description frontmatter; the native loader finds them when needed.</small>
        {(setup.skills_roots ?? []).map(root => <small className="endpoint" key={root}>{root}</small>)}</div>
    </details>
    <small className="set-footnote">Messages and finished answers are sent to Syntarus, and recalled context goes to your chosen model, so avoid pasting secrets into chat. Existing history is not bulk-uploaded.</small>
  </div>;
}
