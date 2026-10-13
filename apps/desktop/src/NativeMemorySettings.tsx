import { useEffect, useState } from "react";
import { nativeProbe, nativeSettings, type NativeSetup } from "./nativeApi";

type Props = { setup: NativeSetup; locked: boolean; onUpdate: (value: NativeSetup) => void; onPendingChange: (value: boolean) => void };

export default function NativeMemorySettings({ setup, locked, onUpdate, onPendingChange }: Props) {
  const [secret, setSecret] = useState("");
  const [enabled, setEnabled] = useState(setup.memory?.enabled ?? false);
  const [scope, setScope] = useState<"project" | "personal">(setup.memory?.scope ?? "project");
  const [identity, setIdentity] = useState(setup.memory?.user_id ?? "");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  useEffect(() => { setEnabled(setup.memory?.enabled ?? false); setScope(setup.memory?.scope ?? "project"); setIdentity(setup.memory?.user_id ?? ""); }, [setup.memory?.enabled, setup.memory?.scope, setup.memory?.user_id]);
  const disabled = locked || pending;
  const run = async (action: "save" | "refresh" | "probe" | "remove") => {
    if (disabled) return;
    if (action === "save" && enabled && !window.confirm("Enable Continuum memory? Smara will send future user messages and completed responses to Syntarus, and send recalled context to your selected model. Messages may include private code or personal information. Existing history, tool logs and skill files will not be bulk-uploaded. Continue?")) return;
    if (action === "probe" && !window.confirm("Send one fixed synthetic search query to Syntarus? No personal conversation, project files or memory writes are included. Provider usage may be charged.")) return;
    if (action === "remove" && !window.confirm("Remove the protected Syntarus key and disable conversation memory? Local history and already-hosted memories will not be deleted.")) return;
    setPending(true); onPendingChange(true); setError(""); setNotice("");
    try {
      if (action === "probe") {
        const result = await nativeProbe("test_memory");
        setNotice(result.detail ?? "Syntarus responded.");
      } else {
        const result = await nativeSettings(action === "save" ? "save_memory" : action === "remove" ? "delete_credential" : "bootstrap", action === "save" ? { secret, memory: { enabled, scope, user_id: identity } } : action === "remove" ? { name: "SYNTARUS_API_KEY" } : {});
        onUpdate(result);
        setNotice(action === "save" ? "Saved. Memory changes apply to the next connection; disabling also stops background sync. No existing history was uploaded." : action === "remove" ? "Key removed and memory disabled. Local history and hosted memories were preserved." : "Local memory status refreshed.");
      }
    } catch (reason) { setError(String(reason)); }
    finally { setSecret(""); setPending(false); onPendingChange(false); }
  };
  return <div className="settings-grid">
    <form className="settings-card settings-form" onSubmit={event => { event.preventDefault(); void run("save"); }}>
      <h2>Conversation memory · Continuum</h2>
      <p>Recall your preferences, decisions and past conversations before answering. Save completed responses for future conversations using the Syntarus Python SDK.</p>
      <label>Syntarus project key<input aria-label="Syntarus project key" type="password" autoComplete="off" disabled={disabled} value={secret} onChange={event => setSecret(event.target.value)} placeholder="Paste your key; leave blank to keep the saved key" /></label>
      <small>Encrypted with Windows DPAPI. The key is never returned to the UI, model, tools or skills.</small>
      <label className="native-tools-choice"><input type="checkbox" disabled={disabled} checked={enabled} onChange={event => setEnabled(event.target.checked)} /> Use Continuum for conversation memory</label>
      <label>Memory scope<select aria-label="Conversation memory scope" disabled={disabled} value={scope} onChange={event => setScope(event.target.value as "project" | "personal")}><option value="project">Separate memory for each project</option><option value="personal">Personal memory shared across my projects</option></select></label>
      <details><summary>Advanced · memory identity</summary><label>Stable user ID<input aria-label="Syntarus memory user ID" disabled={disabled} value={identity} onChange={event => setIdentity(event.target.value)} placeholder="Generated automatically when you first save" /></label><small>Use an existing Syntarus user ID to recall its history. Changing this ID or scope does not migrate memories or pending writes. Projects use an additional isolated agent scope.</small></details>
      <small>Enabling authorizes future messages and completed responses to be sent to Syntarus, including private text you put in chat. Recalled context goes to your selected model. Native history stays local; no automatic history backfill, tool-log upload, or skill upload.</small>
      <button className="primary" disabled={disabled || (enabled && !setup.memory?.configured && !secret.trim())}>Save memory settings</button>
    </form>
    <section className="settings-card">
      <h2>Memory status</h2>
      <p className={`readiness ${setup.memory?.enabled && setup.memory?.configured ? "ready" : "attention"}`}>{setup.memory?.enabled ? "Enabled" : "Disabled"} · {setup.memory?.configured ? "Protected key readable" : "No readable key"}</p>
      <p>{setup.memory?.detail ?? "Save a Syntarus key to get started."}</p>
      {setup.memory?.last_activity && <p role="status">{setup.memory.last_activity.detail} · {new Date(setup.memory.last_activity.updated * 1000).toLocaleString()}</p>}
      <p>Pending: {(setup.memory?.writes.pending ?? 0) + (setup.memory?.writes.submitted ?? 0)} · Processed: {setup.memory?.writes.succeeded ?? 0} · Failed: {setup.memory?.writes.failed ?? 0}</p>
      <small>A queued write is not a processed memory. Syntarus ingestion is asynchronous. Failed writes do not erase native history. Disabling or removing the key stops future syncing but does not delete hosted memories.</small>
      <div className="settings-actions"><button disabled={disabled} onClick={() => void run("refresh")}>Refresh memory status</button><button disabled={disabled || !setup.memory?.configured} onClick={() => void run("probe")}>Test memory connection</button><button disabled={disabled || !setup.memory?.configured} onClick={() => void run("remove")}>Remove memory key</button></div>
      <h3>Procedural memory · local skills</h3>
      <p>Reusable workflows stay in <code>&lt;skill-name&gt;/SKILL.md</code> with name and description frontmatter. The existing native loader discovers and loads skills when needed; Continuum does not replace it.</p>
      {(setup.skills_roots ?? []).map(root => <small className="endpoint" key={root}>{root}</small>)}
      <small>Keep secrets out of skills. The native loader remains authoritative; these paths are not an inventory of every inherited or configured skill.</small>
    </section>
    {error && <p role="alert" className="native-error">{error}</p>}{notice && <p role="status" className="settings-notice">{notice}</p>}
  </div>;
}
