import { useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { nativeSettings, type NativeSetup } from "./nativeApi";

type Draft = { id: string; content_hash: string; state: string; detail: string; message_id?: string;
  draft: { company: string; to: string; subject: string; text: string; evidence: { url: string; claim: string; quote: string }[] } };
type Status = { enabled: boolean; client_id: string; allowlist: string[]; daily_limit: number; connected: boolean; sender: string };
type Props = { workspace: string; locked: boolean; onUpdate: (setup: NativeSetup) => void; onPendingChange: (pending: boolean) => void };
const call = <T,>(action: string, workspace: string, values: Record<string, unknown> = {}) =>
  invoke<T>("native_manage", { request: { operation: "outreach", outreach: { action, workspace, ...values } } });

export default function NativeEmail({ workspace, locked, onUpdate, onPendingChange }: Props) {
  const [status, setStatus] = useState<Status | null>(null), [drafts, setDrafts] = useState<Draft[]>([]);
  const [enabled, setEnabled] = useState(false), [clientId, setClientId] = useState("");
  const [secret, setSecret] = useState(""), [cohort, setCohort] = useState(""), [daily, setDaily] = useState(5);
  const [selected, setSelected] = useState(""), [pending, setPending] = useState(false), [error, setError] = useState("");
  const [notice, setNotice] = useState(""), [editSubject, setEditSubject] = useState(""), [editText, setEditText] = useState("");
  const draft = drafts.find(value => value.id === selected);
  useEffect(() => { setEditSubject(draft?.draft.subject || ""); setEditText(draft?.draft.text || ""); }, [draft?.id, draft?.content_hash]);
  const unsaved = !!draft && (editSubject !== draft.draft.subject || editText !== draft.draft.text);
  const refresh = async () => {
    const value = await call<{ status: Status; drafts: Draft[] }>("list", workspace);
    setStatus(value.status); setDrafts(value.drafts);
    return value.status;
  };
  useEffect(() => { let active = true;
    void refresh().then(value => { if (active) { setEnabled(value.enabled); setClientId(value.client_id); setCohort(value.allowlist.join("\n")); setDaily(value.daily_limit); } }).catch(reason => { if (active) setError(String(reason)); });
    return () => { active = false; };
  }, [workspace]);
  const run = async (action: string, values: Record<string, unknown> = {}) => {
    if (pending || locked) return;
    setPending(true); onPendingChange(true); setError("");
    try {
      const result = await call<{ detail?: string }>(action, workspace, values);
      setNotice(result.detail || "Saved. Review the updated record below.");
      await refresh();
    }
    catch (reason) { setError(String(reason)); }
    finally { setPending(false); onPendingChange(false); }
  };
  const save = async () => {
    if (pending || locked) return;
    if (enabled && !status?.enabled && !window.confirm("Allow Smara to store research-backed LOCAL email drafts? Drafts require native approval and never send automatically. Continue?")) return;
    setPending(true); onPendingChange(true); setError("");
    try {
      onUpdate(await nativeSettings("save_outreach", { outreach: { enabled, client_id: clientId.trim(), daily_limit: daily, allowlist: cohort.split(/[\s,]+/).filter(Boolean) }, client_secret: secret }));
      await refresh();
    } catch (reason) { setError(String(reason)); }
    finally { setSecret(""); setPending(false); onPendingChange(false); }
  };
  return <div className="settings-grid">
    <section className="settings-card settings-form"><h2>Email & company research</h2>
      <p>Research-backed local drafts first. Sending is a reviewed action, not an autonomous model tool.</p>
      {error && <p role="alert" className="native-error">{error}</p>}
      {pending && <p role="status">Working on this email action. Do not repeat an uncertain send.</p>}
      {notice && <p role="status">{notice}</p>}
      <label><input type="checkbox" aria-label="Enable local email drafts" disabled={locked || pending} checked={enabled} onChange={event => setEnabled(event.target.checked)} /> Enable local research-backed drafts</label>
      <label>Google Desktop OAuth client ID<input aria-label="Google Desktop OAuth client ID" disabled={locked || pending} value={clientId} onChange={event => setClientId(event.target.value)} /></label>
      <label>Desktop OAuth client secret<input aria-label="Google Desktop OAuth client secret" type="password" autoComplete="off" disabled={locked || pending} value={secret} onChange={event => setSecret(event.target.value)} placeholder="Saved encrypted; leave blank to retain" /></label>
      <small>Use your own Google Cloud Desktop OAuth client with Gmail API enabled. Consent requests sending and read-back access. Google may require app verification; Smara does not bypass it.</small>
      <label>Approved recipient cohort<textarea aria-label="Approved email recipients" disabled={locked || pending} value={cohort} onChange={event => setCohort(event.target.value)} placeholder="One exact email address per line. Empty means no sending." /></label>
      <label>Maximum attempts per rolling day<input aria-label="Daily email limit" type="number" min="1" max="20" disabled={locked || pending} value={daily} onChange={event => setDaily(Number(event.target.value))} /></label>
      <button disabled={locked || pending} onClick={() => void save()}>Save email settings</button>
      <p>{status?.connected ? `Connected: ${status.sender}` : "Gmail not connected. Drafts do not need an account."}</p>
      <button disabled={locked || pending || !status?.client_id} onClick={() => { if (window.confirm("Open Google's authorization in your system browser? Complete consent yourself. No email will be sent.")) void run("connect"); }}>Connect Gmail…</button>
      <button disabled={locked || pending} onClick={() => { if (window.confirm("Disconnect Gmail locally and cancel pending authorization? Drafts remain. Revoke Google account access separately if needed.")) void run("disconnect"); }}>Disconnect Gmail</button>
      <button disabled={locked || pending} onClick={() => void refresh().catch(reason => setError(String(reason)))}>Refresh account & drafts</button>
      <small>Reconciliation only confirms the Sent record; it does not prove recipient delivery or a reply. No automatic follow-ups.</small>
    </section>
    <section className="settings-card"><h2>Review drafts</h2>
      {!drafts.length && <p>No drafts yet. Enable local drafts, then ask Smara to research companies and prepare evidence-backed emails. It must fetch the company's published contact address.</p>}
      {drafts.map(value => <button key={value.id} disabled={pending} onClick={() => setSelected(value.id)}>{value.draft.company} · {value.state}</button>)}
      {draft && <><h3>{draft.draft.company}</h3><p>To: {draft.draft.to}</p><p>Subject: {draft.draft.subject}</p><pre>{draft.draft.text}</pre>
        {draft.state === "draft" && <details><summary>Edit this draft</summary><label>Subject<input aria-label="Draft subject" disabled={locked || pending} value={editSubject} onChange={event => setEditSubject(event.target.value)} /></label><label>Message<textarea aria-label="Draft message" disabled={locked || pending} value={editText} onChange={event => setEditText(event.target.value)} /></label><button disabled={locked || pending || !unsaved || !editSubject.trim() || !editText.trim()} onClick={() => void run("edit", { id: draft.id, content_hash: draft.content_hash, subject: editSubject, text: editText })}>Save draft revision</button><small>Saving invalidates the old content review. The recipient and source evidence are retained.</small></details>}
        <details open><summary>Personalization evidence — review support</summary>{draft.draft.evidence.map((evidence, index) => <blockquote key={index}><p>{evidence.claim}</p><p>{evidence.quote}</p><a href={evidence.url} onClick={event => { event.preventDefault(); void invoke("native_open_url", { url: evidence.url }); }}>Open source</a></blockquote>)}</details>
        <p>{draft.detail || (draft.state === "draft" ? "Nothing sent. Review every claim, recipient and the full message." : "Dispatch is pending or uncertain. Do not resend; refresh and reconcile.")}</p>
        {draft.state === "draft" && <button disabled={locked || pending || unsaved || !status?.enabled || !status.connected || !status.allowlist.includes(draft.draft.to)} onClick={() => {
          if (window.confirm(`Send this exact email to ${draft.draft.to}?\n\nSubject: ${draft.draft.subject}\n\n${draft.draft.text}\n\nThis is a real external message. Continue?`)) void run("send", { id: draft.id, content_hash: draft.content_hash });
        }}>Review & send one email…</button>}
        {["unknown", "accepted", "sent_verified"].includes(draft.state) && <button disabled={locked || pending} onClick={() => void run("reconcile", { id: draft.id })}>Check Gmail Sent</button>}
        {!["unknown", "sending", "suppressed"].includes(draft.state) && <button disabled={locked || pending} onClick={() => { if (window.confirm(`Do not contact ${draft.draft.to}? This preserves the record and blocks further sends.`)) void run("suppress", { id: draft.id }); }}>Do not contact</button>}
      </>}
    </section>
  </div>;
}
