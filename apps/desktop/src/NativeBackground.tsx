import { useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
type Status = { startup: boolean; service: { running?: boolean; online?: boolean | null }; schedules: { id: string; name: string; enabled: number; runs: number; max_runs: number; due: number; detail: string; goal_state?: string }[] };
export default function NativeBackground({ workspace, profile, locked, onPendingChange }: { workspace: string; profile: string; locked: boolean; onPendingChange: (value: boolean) => void }) {
  const busy = useRef(false);
  const [status, setStatus] = useState<Status | null>(null), [error, setError] = useState(""), [pending, setPending] = useState(false);
  const [prompt, setPrompt] = useState(""), [name, setName] = useState(""), [interval, setInterval] = useState(3600), [maximum, setMaximum] = useState(10);
  const request = async (action = "status", values: Record<string, unknown> = {}) => {
    if (busy.current) return; busy.current = true; setPending(true); onPendingChange(true); setError("");
    try { setStatus(await invoke<Status>("native_manage", { request: { operation: "background", action, workspace, ...values } })); }
    catch (reason) { setError(String(reason)); } finally { busy.current = false; setPending(false); onPendingChange(false); }
  };
  useEffect(() => { void request(); }, [workspace]);
  return <section className="settings-card settings-form"><h2>Background work</h2>
    <p>Chat, Telegram and recurring wakes use one goal worker. Keep the laptop awake and online. Interrupted tasks stay paused; completed effects are not replayed.</p>
    {error && <p role="alert">{error}</p>}<p role="status">{status?.service.running ? status.service.online === false ? "Worker running · offline; queued tasks wait" : "Worker running" : "Worker stopped"}</p>
    <button disabled={locked || pending} onClick={() => void request(status?.service.running ? "stop_service" : "start_service")}>{status?.service.running ? "Stop background work" : "Start background work"}</button><button disabled={pending} onClick={() => void request()}>Refresh status</button>
    <label><input type="checkbox" checked={status?.startup ?? false} disabled={locked || pending} onChange={event => {
      const enabled = event.target.checked;
      if (window.confirm(enabled ? "Start the verified Smara worker at Windows sign-in? Enabled schedules can incur charges within their displayed budgets. Interrupted tasks still pause." : "Disable Smara background startup? This does not stop the current worker.")) void request("startup", { enabled });
    }} /> Start at Windows sign-in (opt-in)</label>
    <form onSubmit={event => { event.preventDefault(); void request("add", { name, interval_seconds: interval, max_runs: maximum,
      spec: { workspace, profile_id: profile, objective: prompt, success_criteria: "Provide sourced evidence and outputs for human review", task_kind: "research", max_requests: 4, max_cycles: 2, max_token_estimate: 500000, cycle_seconds: 900 } }); }}>
      <h3>Recurring research</h3><small>Created paused. Each wake: read-only, 4 requests, 2 cycles, 500k conservative estimate. The next wake waits for acceptance. Missed wakes do not accumulate.</small>
      <label>Name<input required value={name} disabled={locked || pending} onChange={event => setName(event.target.value)} /></label>
      <label>Outcome<textarea required value={prompt} disabled={locked || pending} onChange={event => setPrompt(event.target.value)} /></label>
      <label>Seconds between wakes<input disabled={locked || pending} type="number" min={60} max={31536000} value={interval} onChange={event => setInterval(Number(event.target.value))} /></label>
      <label>Maximum wakes<input disabled={locked || pending} type="number" min={1} max={100} value={maximum} onChange={event => setMaximum(Number(event.target.value))} /></label><button disabled={locked || pending || !profile}>Save paused schedule</button>
    </form>
    {status?.schedules.map(item => <article key={item.id}><strong>{item.name}</strong><p>{item.runs}/{item.max_runs} wakes · {item.goal_state || "No run yet"} · {item.detail}</p><small>Next eligible: {new Date(item.due * 1000).toLocaleString()}</small><button disabled={locked || pending} onClick={() => {
      if (item.enabled || window.confirm("Enable this bounded schedule? Provider requests can incur charges. Review the prior result before another wake.")) void request(item.enabled ? "pause" : "enable", { id: item.id });
    }}>{item.enabled ? "Pause schedule" : "Enable schedule"}</button></article>)}
  </section>;
}
