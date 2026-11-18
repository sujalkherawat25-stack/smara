import { useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { goalLabel, goalRequest, type Goal, type GoalList } from "./goalApi";
import type { AgentRoster } from "./agents";
import { supportsApproval } from "./nativeView";
import { actionLabel, timeLabel, type AuditEntry } from "./auditView";
import { approvalText, byColumn, canDismiss, canPause, canResume, canStop, COLUMNS, inputLine, projectName, title } from "./taskBoardView";

type Props = {
  projects: { workspace: string; label: string }[]; workspace: string; profile: string; roster: AgentRoster; locked: boolean;
  onClose: () => void;
};

/** Several agents working at once: one card per background task, with its live step, approvals, controls and log. */
export default function NativeTaskBoard({ projects, workspace, profile, roster, locked, onClose }: Props) {
  const [list, setList] = useState<GoalList>({ goals: [], service: {} });
  const [details, setDetails] = useState<Record<string, Goal>>({});
  const [open, setOpen] = useState("");
  const [log, setLog] = useState<AuditEntry[]>([]);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState({ objective: "", criteria: "", agent: roster.default, project: workspace, edit: false, apps: false });
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const receipt = useRef(crypto.randomUUID().replaceAll("-", ""));

  const refresh = async () => {
    const result = await goalRequest<GoalList>("list");
    setList(result);
    // Approvals and the event log come with the full record; fetch it only for cards that need it.
    const wanted = result.goals.filter(goal => goal.state === "awaiting_approval" || goal.id === open);
    const fetched = await Promise.all(wanted.map(goal => goalRequest<Goal>("get", { id: goal.id }).catch(() => null)));
    setDetails(Object.fromEntries(fetched.filter((goal): goal is Goal => !!goal).map(goal => [goal.id, goal])));
    if (open) {
      const audit = await invoke<{ entries: AuditEntry[] }>("native_manage", { request: { operation: "audit", action: "list", task: open, limit: 50 } }).catch(() => ({ entries: [] }));
      setLog(audit.entries);
    }
  };
  useEffect(() => {
    let disposed = false, polling = false;
    const poll = async () => {
      if (disposed || polling) return;
      polling = true;
      try { await refresh(); } catch (reason) { if (!disposed) setError(String(reason)); } finally { polling = false; }
    };
    void poll();
    const timer = setInterval(() => void poll(), 2500);
    return () => { disposed = true; clearInterval(timer); };
  }, [open]);

  const act = async (action: string, values: Record<string, unknown> = {}) => {
    if (pending) return false;
    setPending(true); setError("");
    try { await goalRequest(action, values); await refresh(); return true; }
    catch (reason) { setError(String(reason)); return false; }
    finally { setPending(false); }
  };
  const create = async () => {
    const ok = await act("create", {
      identity: receipt.current, objective: form.objective, success_criteria: form.criteria || "The owner reviews the result",
      workspace: form.project, profile_id: profile, sandbox: form.edit ? "workspace-write" : "read-only",
      agent_id: form.agent, use_apps: form.apps, task_kind: "general",
    });
    if (ok) { receipt.current = crypto.randomUUID().replaceAll("-", ""); setCreating(false); setForm(current => ({ ...current, objective: "", criteria: "" })); }
  };
  const agentOf = (goal: Goal) => roster.roster.find(agent => agent.id === goal.spec.agent_id);
  const columns = byColumn(list.goals.map(goal => details[goal.id] ? { ...goal, approvals: details[goal.id].approvals } : goal));
  const limit = list.parallel_limit ?? 4;

  return <section className="task-board" aria-label="Task board">
    <header className="task-board-head">
      <div><h2>Task board</h2><p>Give work to your agents. Several tasks can run at once; only one uses the mouse and keyboard at a time.</p></div>
      <button onClick={onClose}>Back to chat</button>
    </header>
    {error && <p className="native-error" role="alert">{error}</p>}
    <div className="task-board-bar">
      <span className={list.service.running ? "pill on" : "pill"}>{list.service.running ? "Background work is on" : "Background work is off"}</span>
      <button disabled={pending} onClick={() => void act(list.service.running ? "stop_service" : "start_service")}>{list.service.running ? "Stop all background work" : "Start background work"}</button>
      <label>At once
        <select aria-label="Tasks at once" disabled={pending || locked} value={list.max_parallel ?? 2} onChange={event => void act("set_parallel", { max_parallel: Number(event.target.value) })}>
          {Array.from({ length: limit }, (_, n) => n + 1).map(n => <option key={n} value={n}>{n}</option>)}
        </select>
      </label>
      <small>{inputLine(list.input)}</small>
      <button className="primary" disabled={locked || pending || !profile || !projects.length} onClick={() => { setCreating(!creating); setError(""); }}>{creating ? "Cancel" : "New task"}</button>
    </div>

    {creating && <form className="settings-form task-new" onSubmit={event => { event.preventDefault(); void create(); }}>
      <label>What should be done?<textarea required value={form.objective} onChange={event => setForm({ ...form, objective: event.target.value })} placeholder="For example: Download the GSTR-2B for October from the GST portal and compare it with the purchase register" /></label>
      <label>How will you know it is done? (optional)<textarea value={form.criteria} onChange={event => setForm({ ...form, criteria: event.target.value })} placeholder="For example: a sheet listing every invoice that does not match, with totals" /></label>
      <div className="form-pair">
        <label>Agent<select value={form.agent} onChange={event => setForm({ ...form, agent: event.target.value })}>{roster.roster.map(agent => <option key={agent.id} value={agent.id}>{agent.icon} {agent.name} · {agent.role}</option>)}</select></label>
        <label>Project<select value={form.project} onChange={event => setForm({ ...form, project: event.target.value })}>{projects.map(project => <option key={project.workspace} value={project.workspace}>{project.label}</option>)}</select></label>
      </div>
      <label className="native-tools-choice"><input type="checkbox" checked={form.edit} onChange={event => setForm({ ...form, edit: event.target.checked })} /> May create and change files in this project (it then works alone in the project)</label>
      <label className="native-tools-choice"><input type="checkbox" checked={form.apps} onChange={event => setForm({ ...form, apps: event.target.checked })} /> May use my apps (Tally, Excel…) while app control is allowed in Settings. The Smara bar shows when it acts.</label>
      <button className="primary" disabled={pending || locked || !form.objective.trim()}>Give this task</button>
      <small>Send, pay, submit, delete and similar steps still wait for your approval here.</small>
    </form>}

    <div className="task-columns">
      {COLUMNS.map(column => <div key={column.id} className={`task-column ${column.id}`}>
        <h3>{column.label} <small>{columns[column.id].length}</small></h3>
        {columns[column.id].map(goal => {
          const agent = agentOf(goal);
          const full = details[goal.id];
          return <article key={goal.id} className="task-card">
            <div className="task-card-head"><span className="task-agent" title={agent?.role}>{agent ? `${agent.icon} ${agent.name}` : "Smara"}</span><span className="task-state">{goalLabel(goal.state)}</span></div>
            <strong>{title(goal)}</strong>
            <small>{projectName(goal.spec.workspace, projects)}{goal.spec.sandbox === "workspace-write" ? " · edits files" : ""}{goal.spec.use_apps ? " · uses apps" : ""} · {goal.cycles}/{goal.spec.max_cycles} rounds</small>
            {goal.detail && <p className="task-step">{goal.detail}</p>}
            {full?.approvals?.map(request => <div key={request.id} className="task-approval">
              <p>{approvalText(request)}</p>
              <button className="primary" disabled={pending || !supportsApproval(request.method, request.params)} onClick={() => void act("respond", { id: goal.id, request_id: request.id, allow: true })}>Approve</button>
              <button disabled={pending || !supportsApproval(request.method, request.params)} onClick={() => void act("respond", { id: goal.id, request_id: request.id, allow: false })}>Deny</button>
            </div>)}
            <div className="task-actions">
              {canPause(goal) && <button disabled={pending} onClick={() => void act("pause", { id: goal.id })}>Pause</button>}
              {canResume(goal) && <button disabled={pending} onClick={() => void act("resume", { id: goal.id })}>Resume</button>}
              {goal.state === "ready_for_review" && <button className="primary" disabled={pending} onClick={() => void act("accept", { id: goal.id })}>Accept result</button>}
              {canStop(goal) && <button disabled={pending} onClick={() => { if (window.confirm("Stop this task? Its work so far is kept, but it will not continue.")) void act("stop", { id: goal.id }); }}>Stop</button>}
              {canDismiss(goal) && <button disabled={pending} onClick={() => void act("dismiss", { id: goal.id })}>Remove</button>}
              <button className="link" onClick={() => { setOpen(open === goal.id ? "" : goal.id); setLog([]); }}>{open === goal.id ? "Hide log" : "Log"}</button>
            </div>
            {open === goal.id && <div className="task-log">
              {full?.checkpoint?.summary && <p>{full.checkpoint.summary.slice(0, 600)}</p>}
              {full?.checkpoint?.next_step && <p><b>Next:</b> {full.checkpoint.next_step}</p>}
              <h4>Steps on this PC today</h4>
              {log.length ? <ol>{log.map(entry => <li key={entry.id}><time>{timeLabel(entry.ts)}</time> {actionLabel(entry)}{entry.target && ` · ${entry.target}`}</li>)}</ol> : <p>No steps recorded today.</p>}
              <h4>Progress</h4>
              <ol>{(full?.events ?? []).slice(-12).map(event => <li key={event.seq}>{event.body.summary || event.kind}</li>)}</ol>
            </div>}
          </article>;
        })}
      </div>)}
    </div>
  </section>;
}
