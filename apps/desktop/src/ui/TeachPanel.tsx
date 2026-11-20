import { useCallback, useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import type { AgentRecord } from "../agents";
import { normalizeUrl, startProblem, teachLine, type TeachState } from "../teach";

const call = (action: string, values: Record<string, unknown> = {}) =>
  invoke<TeachState>("native_manage", { request: { operation: "teach", action, ...values } });

/** Teach by showing: the owner does a job once in Smara's browser; an employee learns it from the list of steps. */
export default function TeachPanel({ employees, locked, onTeach, onClose }: { employees: AgentRecord[]; locked: boolean; onTeach: (agentId: string, lesson: string) => void; onClose: () => void }) {
  const [state, setState] = useState<TeachState>({ state: "idle", detail: "", start_url: "", steps: [], count: 0 });
  const [employee, setEmployee] = useState(employees[0]?.id ?? "");
  const [url, setUrl] = useState("");
  const [skill, setSkill] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => { if (!employee && employees[0]) setEmployee(employees[0].id); }, [employees, employee]);
  const refresh = useCallback(async () => { try { const next = await call("status"); if (alive.current) setState(next); } catch (reason) { if (alive.current) setError(String(reason)); } }, []);
  useEffect(() => { void refresh(); }, [refresh]);
  useEffect(() => {
    if (state.state !== "recording") return;
    const timer = window.setInterval(() => void refresh(), 1500);
    return () => window.clearInterval(timer);
  }, [state.state, refresh]);
  const act = async (work: () => Promise<TeachState>) => {
    setBusy(true); setError("");
    try { const next = await work(); if (alive.current) setState(next); } catch (reason) { if (alive.current) setError(String(reason)); } finally { if (alive.current) setBusy(false); }
  };
  const problem = startProblem(url, employee);
  const person = employees.find(agent => agent.id === employee);
  const recording = state.state === "recording";
  const teachNow = async () => {
    setBusy(true); setError("");
    try {
      const result = await call("lesson", { agent: person?.name ?? "Smara", name: skill });
      if (result.lesson) { onTeach(employee, result.lesson); await call("discard"); }
    } catch (reason) { if (alive.current) setError(String(reason)); } finally { if (alive.current) setBusy(false); }
  };

  return <section className="hosted-panel teach-panel" aria-label="Teach by showing">
    <header className="task-board-head"><div><h2>Teach by showing</h2><p>Do a job once in Smara's browser. An employee learns it and does it the same way next time.</p></div><button className="text-button" onClick={onClose}>Close</button></header>
    {error && <p role="alert" className="native-error">{error}</p>}
    {employees.length === 0 ? <p className="inbox-empty">Hire an employee first, then teach them.</p> : <>
      <form className="settings-form" onSubmit={event => { event.preventDefault(); if (!problem && !recording) void act(() => call("start", { url: normalizeUrl(url) })); }}>
        <label>Teach<select value={employee} disabled={recording} onChange={event => setEmployee(event.target.value)}>{employees.map(agent => <option key={agent.id} value={agent.id}>{agent.name} · {agent.role}</option>)}</select></label>
        <label>Where the job starts<input value={url} disabled={recording} autoComplete="off" placeholder="example.com/orders" onChange={event => setUrl(event.target.value)} /></label>
        <label>Name for the skill <small>(optional)</small><input value={skill} autoComplete="off" placeholder="Monthly GST report" onChange={event => setSkill(event.target.value)} /></label>
        <div className="settings-actions">
          {!recording && <button className="primary" disabled={busy || locked || !!problem}>{state.count ? "Record again" : "Start"}</button>}
          {recording && <button type="button" className="primary" disabled={busy} onClick={() => void act(() => call("stop"))}>Done</button>}
          {state.count > 0 && !recording && <button type="button" disabled={busy} onClick={() => void act(() => call("discard"))}>Discard</button>}
        </div>
        <small>{teachLine(state)}</small>
        <small>Only what you do in that window is written down. Passwords, codes and card details are never recorded. You type those yourself.</small>
      </form>
      {state.steps.length > 0 && <ol className="teach-steps" aria-label="Recorded steps">{state.steps.map(step => <li key={step.n}><span>{step.text}</span>{!recording && <button className="text-button" aria-label={`Remove step ${step.n + 1}`} disabled={busy} onClick={() => void act(() => call("remove", { step: step.n }))}>Remove</button>}</li>)}</ol>}
      {state.state === "stopped" && state.count > 0 && <div className="settings-actions"><button className="primary" disabled={busy || locked} onClick={() => void teachNow()}>Teach {person?.name ?? "them"} this</button></div>}
    </>}
  </section>;
}
