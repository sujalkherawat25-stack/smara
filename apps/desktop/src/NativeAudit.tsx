import { useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { actionLabel, failed, filterEntries, sizeLabel, timeLabel, whoLabel, type AuditDay, type AuditEntry } from "./auditView";

type Request = { action: "days" | "list" | "shot" | "delete"; day?: string; path?: string; all?: boolean; limit?: number };
const audit = <T,>(request: Request) => invoke<T>("native_manage", { request: { operation: "audit", ...request } });

/** Activity log: every step an agent took on this PC (browser, apps, approvals), kept only here, deletable. */
export default function NativeAudit({ locked }: { locked: boolean }) {
  const [days, setDays] = useState<AuditDay[]>([]);
  const [folder, setFolder] = useState("");
  const [day, setDay] = useState("");
  const [rows, setRows] = useState<AuditEntry[]>([]);
  const [kind, setKind] = useState("all");
  const [query, setQuery] = useState("");
  const [shot, setShot] = useState<{ id: string; src: string } | null>(null);
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);

  const loadDays = async () => {
    const result = await audit<{ days: AuditDay[]; folder: string }>({ action: "days" });
    setDays(result.days); setFolder(result.folder);
    return result.days;
  };
  const loadDay = async (value: string) => {
    setDay(value); setShot(null);
    setRows(value ? (await audit<{ entries: AuditEntry[] }>({ action: "list", day: value, limit: 1000 })).entries : []);
  };
  const run = async (work: () => Promise<void>) => {
    if (pending) return;
    setPending(true); setError("");
    try { await work(); } catch (reason) { setError(String(reason)); } finally { setPending(false); }
  };
  useEffect(() => { void run(async () => { const found = await loadDays(); await loadDay(found[0]?.day ?? ""); }); }, []);

  const showShot = (entry: AuditEntry) => run(async () => {
    if (shot?.id === entry.id) { setShot(null); return; }
    const result = await audit<{ png_base64: string }>({ action: "shot", path: entry.screenshot });
    setShot({ id: entry.id, src: `data:image/${entry.screenshot.endsWith(".jpg") ? "jpeg" : "png"};base64,${result.png_base64}` });
  });
  const remove = (all: boolean) => run(async () => {
    const what = all ? "the whole activity log (every day and every screenshot)" : `the activity log for ${day}`;
    if (!window.confirm(`Delete ${what}? This cannot be undone.`)) return;
    await audit(all ? { action: "delete", all: true } : { action: "delete", day });
    const found = await loadDays();
    await loadDay(found[0]?.day ?? "");
  });

  const shown = filterEntries(rows, kind, query);
  return <div className="set-page audit-page">
    <header><h2>Activity log</h2><p>Every step Smara took on this PC: pages it opened, what it clicked and typed, apps it used, files it moved, and what you approved. It stays on this PC only. Days older than 30 are removed by themselves.</p></header>
    {error && <p role="alert">{error}</p>}
    <div className="set-actions">
      <select aria-label="Day" disabled={pending} value={day} onChange={event => void run(() => loadDay(event.target.value))}>
        {days.length === 0 && <option value="">No activity yet</option>}
        {days.map(item => <option key={item.day} value={item.day}>{item.day} · {item.entries} steps · {sizeLabel(item.bytes)}</option>)}
      </select>
      <select aria-label="Kind" value={kind} onChange={event => setKind(event.target.value)}>
        <option value="all">Everything</option><option value="browser">Browser</option><option value="computer">Apps</option>
        <option value="approval">Approvals</option><option value="tool">Other tools</option>
      </select>
      <input aria-label="Search the log" placeholder="Search" value={query} onChange={event => setQuery(event.target.value)} />
      <button disabled={pending || !day} onClick={() => void run(() => loadDay(day))}>Refresh</button>
    </div>
    <ol className="audit-list">
      {shown.map(entry => <li key={entry.id} className={failed(entry) ? "audit-row failed" : "audit-row"}>
        <time>{timeLabel(entry.ts)}</time>
        <span className="audit-who">{whoLabel(entry)}</span>
        <span className="audit-what"><b>{actionLabel(entry)}</b>{entry.target && <> · <span>{entry.target}</span></>}</span>
        {entry.outcome && <small className="audit-outcome">{entry.outcome}</small>}
        {entry.screenshot && <button className="link" disabled={pending} onClick={() => void showShot(entry)}>{shot?.id === entry.id ? "Hide screenshot" : "Screenshot"}</button>}
        {shot?.id === entry.id && <img className="audit-shot" alt={`Screen after: ${actionLabel(entry)}`} src={shot.src} />}
      </li>)}
      {day && shown.length === 0 && <li className="audit-empty">Nothing matches.</li>}
    </ol>
    <div className="set-actions">
      <button disabled={locked || pending || !day} onClick={() => void remove(false)}>Delete this day</button>
      <button disabled={locked || pending || days.length === 0} onClick={() => void remove(true)}>Delete everything</button>
    </div>
    {folder && <small>Kept in {folder}</small>}
  </div>;
}
