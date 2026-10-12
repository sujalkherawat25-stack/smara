import { useEffect, useRef, useState } from "react";
import { goalLabel, goalRequest, type Goal, type GoalList } from "./goalApi";
import { supportsApproval } from "./nativeView";
import NativeMarkdown from "./NativeMarkdown";

export default function NativeGoals({ workspace, profile, locked, onClose, onPreview }: {
  workspace: string; profile: string; locked: boolean; onClose: () => void; onPreview: (path: string) => void;
}) {
  const [list, setList] = useState<GoalList>({ goals: [], service: {} });
  const [selected, setSelected] = useState("");
  const [goal, setGoal] = useState<Goal | null>(null);
  const [creating, setCreating] = useState(false);
  const [objective, setObjective] = useState("");
  const [criteria, setCriteria] = useState("");
  const [taskKind, setTaskKind] = useState("general");
  const [editing, setEditing] = useState(false);
  const [feedback, setFeedback] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [limits, setLimits] = useState({ max_cycles: 8, max_requests: 30, max_token_estimate: 500000 });
  const selection = useRef("");
  const creationReceipt = useRef(crypto.randomUUID().replaceAll("-", ""));
  const refresh = async () => {
    const result = await goalRequest<GoalList>("list", { workspace });
    setList(result);
    const identity = selection.current;
    if (identity) {
      const detail = await goalRequest<Goal>("get", { id: identity });
      if (selection.current === identity) setGoal(detail);
    }
  };
  useEffect(() => {
    let disposed = false, polling = false;
    const poll = async () => {
      if (disposed || polling) return;
      polling = true;
      try { await refresh(); } catch (reason) { if (!disposed) setError(String(reason)); }
      finally { polling = false; }
    };
    void poll();
    const timer = setInterval(() => void poll(), 2500);
    return () => { disposed = true; clearInterval(timer); };
  }, [workspace]);
  const act = async (action: string, values: Record<string, unknown> = {}) => {
    if (pending) return false;
    setPending(true); setError("");
    try {
      const value = await goalRequest<Goal>(action, values);
      if (value.id) { selection.current = value.id; setSelected(value.id); setGoal(value); setCreating(false); }
      await refresh();
      return true;
    } catch (reason) { setError(String(reason)); return false; }
    finally { setPending(false); }
  };
  return <section className="native-goals" aria-label="Persistent goals">
    <div className="goals-heading"><div><h2>Goals</h2><p>Give Smara an outcome, follow its progress, and review the result.</p></div><button onClick={onClose}>Back to chat</button></div>
    {error && <p className="native-error" role="alert">{error}</p>}
    <div className="goals-service"><span>{list.service.running ? "● Background work is running" : "○ Background work is stopped"}</span><button disabled={pending} onClick={() => void act(list.service.running ? "stop_service" : "start_service")}>{list.service.running ? "Stop background work" : "Start background work"}</button><small>The laptop must stay awake and online. Pending goals survive restarting Smara.</small></div>
    <div className="goals-layout"><aside className="goals-list"><button disabled={locked || pending || !profile || !workspace} onClick={() => { creationReceipt.current = crypto.randomUUID().replaceAll("-", ""); setLimits({ max_cycles: 8, max_requests: 30, max_token_estimate: 500000 }); setCreating(true); setError(""); }}>＋ New goal</button>
      {list.goals.map(item => <button key={item.id} className={selected === item.id && !creating ? "selected" : ""} onClick={() => { selection.current = item.id; setSelected(item.id); setGoal(item); setLimits({ max_cycles: item.spec.max_cycles, max_requests: item.spec.max_requests, max_token_estimate: item.spec.max_token_estimate }); setCreating(false); void refresh(); }}><strong>{item.spec.objective}</strong><small>{goalLabel(item.state)} · {item.id.slice(0, 8)}</small></button>)}
      {!list.goals.length && <p>No goals in this project yet.</p>}
    </aside><div className="goal-detail">
      {creating ? <form className="settings-form" onSubmit={event => { event.preventDefault(); void act("create", { identity: creationReceipt.current, objective, success_criteria: criteria, task_kind: taskKind, workspace, profile_id: profile, sandbox: editing ? "workspace-write" : "read-only", ...limits }); }}>
        <h3>What should Smara accomplish?</h3><label>Goal<textarea required value={objective} onChange={event => setObjective(event.target.value)} placeholder="Describe the result you need…" /></label>
        <label>How will you judge the result?<textarea required value={criteria} onChange={event => setCriteria(event.target.value)} placeholder="Deliverables, evidence, tests, or questions the result must answer…" /></label>
        <label>Outcome checks<select value={taskKind} onChange={event => setTaskKind(event.target.value)}><option value="general">General · your review</option><option value="coding">Coding · native test evidence</option><option value="research">Research · source reads and citation excerpts</option><option value="mixed">Mixed · tests and sources</option></select></label>
        <small>Checks are evidence gates, not a guarantee of correctness. Research needs two source pages and exact citation excerpts. You still review answer coverage.</small>
        <label className="native-tools-choice"><input type="checkbox" checked={editing} onChange={event => setEditing(event.target.checked)} /> Allow editing files in this project</label>
        <details><summary>Work limits</summary><div className="form-pair">{Object.entries(limits).map(([key, value]) => <label key={key}>{key === "max_cycles" ? "Cycles" : key === "max_requests" ? "Model requests" : "Conservative token estimate"}<input required type="number" min="1" value={value} onChange={event => setLimits(current => ({ ...current, [key]: Number(event.target.value) }))} /></label>)}</div></details>
        <button className="primary" disabled={pending || locked}>Queue goal</button><small>Start background work above to run queued goals. Actions needing more access ask for your response.</small>
      </form> : goal ? <>
        <div className="goal-state"><h3>{goal.spec.objective}</h3><span>{goalLabel(goal.state)}</span></div>
        <p>{goal.detail}</p><dl><dt>Success criteria</dt><dd>{goal.spec.success_criteria}</dd><dt>Work used</dt><dd>{goal.cycles}/{goal.spec.max_cycles} cycles · {goal.requests}/{goal.spec.max_requests} model requests · {goal.token_estimate.toLocaleString()} conservative token estimate</dd></dl>
        <div className="settings-actions">{!["completed", "stopped"].includes(goal.state) && <><button disabled={pending || goal.state === "paused"} onClick={() => void act("pause", { id: goal.id })}>Pause</button><button disabled={pending || !!goal.lease || !["paused", "blocked"].includes(goal.state)} onClick={() => void act("resume", { id: goal.id })}>Resume</button><button disabled={pending} onClick={() => void act("stop", { id: goal.id })}>Stop</button></>}{goal.state === "ready_for_review" && <button className="primary" disabled={pending} onClick={() => void act("accept", { id: goal.id })}>Accept result</button>}</div>
        {goal.approvals?.map(request => <section className="native-approval" key={request.id}><strong>Smara needs your response</strong><details open><summary>{request.method}</summary><pre>{JSON.stringify(request.params, null, 2)}</pre></details><button disabled={pending || !supportsApproval(request.method, request.params)} onClick={() => void act("respond", { id: goal.id, request_id: request.id, allow: true })}>Allow this request</button><button disabled={pending || !supportsApproval(request.method, request.params)} onClick={() => void act("respond", { id: goal.id, request_id: request.id, allow: false })}>Deny</button>{!supportsApproval(request.method, request.params) && <small>Pause this goal and add feedback for this specialized request.</small>}</section>)}
        {goal.checkpoint.summary && <NativeMarkdown text={goal.checkpoint.summary} />}
        {!!goal.checkpoint.completed_steps?.length && <><h4>Finished this cycle</h4><ul>{goal.checkpoint.completed_steps.map((step, index) => <li key={index}>{step}</li>)}</ul></>}
        {goal.checkpoint.outcome && <section className="goal-outcome"><h4>Outcome evidence · {goal.checkpoint.outcome.passed ? "Checks passed; review required" : "Gaps remain"}</h4><small>Last cycle: {goal.checkpoint.outcome.elapsed_seconds}s · {goal.checkpoint.outcome.sources.length} source pages · {goal.checkpoint.outcome.failed_pages.length} failed page reads</small>{goal.checkpoint.outcome.checks.map(check => <p key={check.name}><strong>{check.passed ? "✓" : "○"} {check.name}</strong> — {check.detail}</p>)}<details><summary>Claim-to-source review</summary>{goal.checkpoint.outcome.claims.map((claim, index) => <blockquote key={index}><p>{claim.claim}</p><NativeMarkdown text={`[Source](${claim.url})`} /><p>“{claim.quote}”</p><small>{claim.quote_found ? "Exact excerpt found in a page read by Smara" : "Excerpt not found in observed sources"}. Review whether it supports the claim.</small></blockquote>)}</details></section>}
        {!!goal.checkpoint.plan?.length && <><h4>Plan</h4><ol>{goal.checkpoint.plan.map((step, index) => <li key={index}>{step}</li>)}</ol></>}
        {goal.checkpoint.next_step && <p><strong>Next:</strong> {goal.checkpoint.next_step}</p>}
        {!!goal.checkpoint.evidence?.length && <><h4>Evidence</h4><ul>{goal.checkpoint.evidence.map((entry, index) => <li key={index}><NativeMarkdown text={entry} /></li>)}</ul></>}
        {!!goal.checkpoint.outputs?.length && <><h4>Outputs</h4>{goal.checkpoint.outputs.map(path => <button key={path} onClick={() => onPreview(path)}>{path}</button>)}</>}
        {!["completed", "stopped"].includes(goal.state) && <form className="settings-form" onSubmit={event => { event.preventDefault(); void act("feedback", { id: goal.id, text: feedback }).then(saved => { if (saved) setFeedback(""); }); }}><label>Feedback<textarea required value={feedback} onChange={event => setFeedback(event.target.value)} placeholder="Correct the direction or add missing information…" /></label><button disabled={pending || !feedback.trim()}>Save feedback</button></form>}
        {!["completed", "stopped"].includes(goal.state) && !goal.lease && <details><summary>Extend work limits</summary><form className="settings-form" onSubmit={event => { event.preventDefault(); void act("extend", { id: goal.id, ...limits }); }}><small>These are cumulative limits, including work already used.</small>{Object.entries(limits).map(([key, value]) => <label key={key}>{key.replaceAll("_", " ")}<input type="number" min="1" value={value} onChange={event => setLimits(current => ({ ...current, [key]: Number(event.target.value) }))} /></label>)}<button disabled={pending}>Save limits</button></form></details>}
        <details><summary>Recent activity</summary>{goal.events?.map(event => <p key={event.seq}>{event.body.summary || event.kind}{event.body.failure && <small> Failure stage: {event.body.failure.stage.replaceAll("_", " ")} · {event.body.failure.error_type}{event.body.failure.context ? ` · ${event.body.failure.context.replaceAll("_", " ")}` : ""}</small>}</p>)}</details>
        {goal.session_id && <small>Saved native conversation: {goal.session_id}</small>}
      </> : <p>Select a goal or create one for this project.</p>}
    </div></div>
  </section>;
}
