import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Check, CircleAlert, FileText, RefreshCw, Square, X } from "lucide-react";
import {
  cancelSmaraTask,
  createSmaraTask,
  createSmaraResearch,
  decideSmaraTask,
  getSmaraArtifacts,
  getSmaraEvidence,
  getSmaraEvents,
  getSmaraTask,
  getSmaraSteps,
  listSmaraTasks,
  retrySmaraTask,
  streamSmaraEvents,
  type SmaraArtifact,
  type SmaraEvidence,
  type SmaraEvent,
  type SmaraStep,
  type SmaraTask,
} from "@/lib/smaraWork";

const statusColor: Record<string, string> = {
  queued: "#94a3b8", running: "#60a5fa", waiting_approval: "#f59e0b",
  completed: "#34d399", failed: "#f87171", cancelled: "#94a3b8", cancelling: "#f59e0b",
};

function eventType(event: SmaraEvent): string {
  return event.type || event.event_type || "task event";
}

function eventPayload(event: SmaraEvent): Record<string, unknown> {
  if (event.payload && typeof event.payload === "object") return event.payload;
  if (typeof event.payload !== "string" || !event.payload.trim()) return {};
  try {
    const parsed: unknown = JSON.parse(event.payload);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? parsed as Record<string, unknown>
      : {};
  } catch { return {}; }
}

function eventLabel(event: SmaraEvent): string {
  if (event.message?.trim()) return event.message;
  const type = eventType(event);
  const payload = eventPayload(event);
  if (typeof payload.source === "string") return `${type.replaceAll(".", " ")} · ${payload.source}`;
  return type.replaceAll(".", " ");
}

function latestResult(events: SmaraEvent[]): string | null {
  const ignored = new Set(["recorded", "completed", "succeeded", "success", "ok"]);
  for (const event of [...events].reverse()) {
    const value = eventPayload(event).result;
    // `task.completed` carries the bookkeeping marker "recorded". Prefer
    // the meaningful `step.completed` result instead of showing that marker
    // as if it were the agent's answer.
    if (typeof value === "string" && value.trim() && !ignored.has(value.trim().toLowerCase())) return value.trim();
  }
  return null;
}

function activityEvents(events: SmaraEvent[]): { visible: SmaraEvent[]; collapsed: number } {
  // Heartbeats/progress are useful for liveness, but rendering every one makes
  // a long-running task look noisy and causes unnecessary layout work.
  const noisy = new Set(["executor.heartbeat", "executor.progress", "worker.heartbeat"]);
  const visible = events.filter((event) => !noisy.has(eventType(event))).slice(-12);
  return { visible, collapsed: Math.max(0, events.length - visible.length) };
}

function parseArtifact(content: string | null): Record<string, unknown> | null {
  if (!content) return null;
  try {
    const parsed: unknown = JSON.parse(content);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? parsed as Record<string, unknown>
      : null;
  } catch { return null; }
}

function workspaceJobFromSteps(steps: SmaraStep[]) {
  const step = steps.find((item) => item.workspace_job && typeof item.workspace_job === "object");
  return step?.workspace_job || null;
}

function workspaceStageResults(artifacts: SmaraArtifact[]) {
  return artifacts.flatMap((artifact) => {
    const value = parseArtifact(artifact.content);
    return value?.stage_result && typeof value.stage_result === "object" && !Array.isArray(value.stage_result)
      ? [value.stage_result as Record<string, unknown>] : [];
  });
}

export default function SmaraWorkPanel() {
  const [tasks, setTasks] = useState<SmaraTask[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [steps, setSteps] = useState<SmaraStep[]>([]);
  const [events, setEvents] = useState<SmaraEvent[]>([]);
  const [evidence, setEvidence] = useState<SmaraEvidence[]>([]);
  const [artifacts, setArtifacts] = useState<SmaraArtifact[]>([]);
  const [result, setResult] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showResearch, setShowResearch] = useState(false);
  const [showTask, setShowTask] = useState(false);
  const [taskTitle, setTaskTitle] = useState("");
  const [taskObjective, setTaskObjective] = useState("");
  const [creatingTask, setCreatingTask] = useState(false);
  const [researchTitle, setResearchTitle] = useState("");
  const [researchQuestion, setResearchQuestion] = useState("");
  const [researchSources, setResearchSources] = useState("");
  const [creatingResearch, setCreatingResearch] = useState(false);
  const streamCursors = useRef<Record<string, string>>({});

  const selected = useMemo(() => tasks.find((task) => task.id === selectedId) ?? null, [tasks, selectedId]);

  const refresh = useCallback(async (keepSelection = true) => {
    setError(null);
    try {
      const next = await listSmaraTasks();
      setTasks(next);
      if (!keepSelection || !next.some((task) => task.id === selectedId)) setSelectedId(next[0]?.id ?? null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not load Smara tasks.");
    } finally {
      setLoading(false);
    }
  }, [selectedId]);

  const loadDetails = useCallback(async (taskId: string) => {
    try {
      const [task, nextSteps, nextEvents, nextEvidence, nextArtifacts] = await Promise.all([
        getSmaraTask(taskId), getSmaraSteps(taskId), getSmaraEvents(taskId), getSmaraEvidence(taskId).catch(() => []), getSmaraArtifacts(taskId),
      ]);
      setTasks((current) => current.map((item) => item.id === task.id ? task : item));
      setSteps(nextSteps); setEvents(nextEvents); setEvidence(nextEvidence); setArtifacts(nextArtifacts);
      setResult(task.result?.trim() || latestResult(nextEvents));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not load task details.");
    }
  }, []);

  useEffect(() => { void refresh(false); }, [refresh]);
  useEffect(() => {
    if (!selectedId) { setSteps([]); setEvents([]); setEvidence([]); setArtifacts([]); setResult(null); return; }
    void loadDetails(selectedId);
    const active = selected?.status === "queued" || selected?.status === "running" || selected?.status === "waiting_approval";
    if (!active) return;
    const controller = new AbortController();
    let repairTimer: number | undefined;
    const scheduleRepair = () => {
      if (repairTimer != null) return;
      repairTimer = window.setTimeout(() => {
        repairTimer = undefined;
        void loadDetails(selectedId);
        void refresh();
      }, 150);
    };
    // Render each durable event immediately, but coalesce the repair fetches so
    // a burst of worker events never produces a burst of API requests.
    void streamSmaraEvents(selectedId, controller.signal, (event) => {
      if (event.id) streamCursors.current[selectedId] = event.id;
      setEvents((current) => current.some((item) => item.id === event.id)
        ? current
        : [...current, event].sort((left, right) => left.created_at.localeCompare(right.created_at)).slice(-200));
      const eventResult = latestResult([event]);
      if (eventResult) setResult(eventResult);
      scheduleRepair();
    }, {
      lastEventId: streamCursors.current[selectedId],
      onReconnect: (lastEventId) => {
        if (lastEventId) streamCursors.current[selectedId] = lastEventId;
        scheduleRepair();
      },
    }).then(() => { if (!controller.signal.aborted) void loadDetails(selectedId); })
      .catch(() => { if (!controller.signal.aborted) { setError("Live task updates paused. Refresh to reconnect."); void loadDetails(selectedId); } });
    const timer = window.setInterval(() => { void refresh(); }, 15_000);
    return () => { controller.abort(); window.clearInterval(timer); if (repairTimer != null) window.clearTimeout(repairTimer); };
  }, [selectedId, selected?.status, loadDetails, refresh]);

  async function decide(approved: boolean) {
    if (!selected) return;
    setBusy(true); setError(null);
    try { const updated = await decideSmaraTask(selected.id, approved); setTasks((current) => current.map((task) => task.id === updated.id ? updated : task)); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Could not update approval."); }
    finally { setBusy(false); }
  }

  async function cancel() {
    if (!selected) return;
    setBusy(true); setError(null);
    try { const updated = await cancelSmaraTask(selected.id); setTasks((current) => current.map((task) => task.id === updated.id ? updated : task)); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Could not cancel this task."); }
    finally { setBusy(false); }
  }

  async function retry() {
    if (!selected) return;
    setBusy(true); setError(null);
    try {
      const updated = await retrySmaraTask(selected.id);
      setTasks((current) => current.map((task) => task.id === updated.id ? updated : task));
      await loadDetails(updated.id);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Could not retry this task."); }
    finally { setBusy(false); }
  }

  async function createResearch() {
    const question = researchQuestion.trim();
    if (question.length < 5) { setError("Write a research question with at least five characters."); return; }
    const sources = researchSources.split(/[,\n]/).map((source) => source.trim()).filter(Boolean).slice(0, 12);
    if (sources.some((source) => { try { const parsed = new URL(source); return !["http:", "https:"].includes(parsed.protocol); } catch { return true; } })) {
      setError("Each source must be a valid http:// or https:// URL."); return;
    }
    setCreatingResearch(true); setError(null);
    try {
      const task = await createSmaraResearch({
        title: researchTitle.trim() || "Smara research",
        question,
        sources,
      });
      setTasks((current) => [task, ...current.filter((item) => item.id !== task.id)]);
      setSelectedId(task.id);
      setResearchTitle(""); setResearchQuestion(""); setResearchSources(""); setShowResearch(false);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not create the research task.");
    } finally { setCreatingResearch(false); }
  }

  async function createTask() {
    const objective = taskObjective.trim();
    if (objective.length < 5) { setError("Describe the task with at least five characters."); return; }
    setCreatingTask(true); setError(null);
    try {
      const task = await createSmaraTask({ title: taskTitle.trim() || "Smara task", objective });
      setTasks((current) => [task, ...current.filter((item) => item.id !== task.id)]);
      setSelectedId(task.id);
      setTaskTitle(""); setTaskObjective(""); setShowTask(false);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not create the task.");
    } finally { setCreatingTask(false); }
  }

  if (loading) return <p className="text-[12px]" style={{ color: "var(--text-muted)" }}>Loading Smara work…</p>;

  return (
    <div className="flex flex-col gap-3 h-full min-h-0 min-w-0">
      <div className="flex items-center justify-between gap-2">
        <p className="text-[13px]" style={{ color: "var(--text-secondary)" }}>{tasks.length ? `${tasks.length} task${tasks.length === 1 ? "" : "s"}` : "No durable work yet."}</p>
        <div className="flex items-center gap-2">
          <button onClick={() => { setShowTask((open) => !open); setShowResearch(false); }} className="px-2.5 py-1.5 rounded-md text-[11px]" style={{ border: "1px solid var(--border-dim)", color: "var(--text-secondary)" }}>{showTask ? "Close task" : "New task"}</button>
          <button onClick={() => { setShowResearch((open) => !open); setShowTask(false); }} className="px-2.5 py-1.5 rounded-md text-[11px]" style={{ border: "1px solid var(--border-dim)", color: "var(--text-secondary)" }}>{showResearch ? "Close research" : "New research"}</button>
          <button onClick={() => void refresh()} className="p-2 rounded-md" style={{ border: "1px solid var(--border-dim)", color: "var(--text-muted)" }} title="Refresh tasks"><RefreshCw size={14} /></button>
        </div>
      </div>
      {showTask && <div className="rounded-xl p-3" style={{ background: "var(--bg-card)", border: "1px solid var(--border-dim)" }}>
        <p className="text-[12px] font-medium" style={{ color: "var(--text-primary)" }}>Start durable work</p>
        <input value={taskTitle} onChange={(event) => setTaskTitle(event.target.value)} placeholder="Title (optional)" className="mt-2 w-full rounded-md px-2.5 py-2 text-[12px] outline-none" style={{ background: "var(--bg-base)", border: "1px solid var(--border-dim)", color: "var(--text-primary)" }} />
        <textarea value={taskObjective} onChange={(event) => setTaskObjective(event.target.value)} placeholder="What should Smara do? Risky actions will wait for approval." rows={3} className="mt-2 w-full rounded-md px-2.5 py-2 text-[12px] outline-none resize-y" style={{ background: "var(--bg-base)", border: "1px solid var(--border-dim)", color: "var(--text-primary)" }} />
        <div className="mt-2 flex justify-end"><button disabled={creatingTask} onClick={() => void createTask()} className="px-3 py-1.5 rounded-md text-[11px]" style={{ background: "var(--accent)", color: "#07111f", opacity: creatingTask ? 0.6 : 1 }}>{creatingTask ? "Creating…" : "Create task"}</button></div>
      </div>}
      {showResearch && <div className="rounded-xl p-3" style={{ background: "var(--bg-card)", border: "1px solid var(--border-dim)" }}>
        <p className="text-[12px] font-medium" style={{ color: "var(--text-primary)" }}>Start cited research</p>
        <input value={researchTitle} onChange={(event) => setResearchTitle(event.target.value)} placeholder="Title (optional)" className="mt-2 w-full rounded-md px-2.5 py-2 text-[12px] outline-none" style={{ background: "var(--bg-base)", border: "1px solid var(--border-dim)", color: "var(--text-primary)" }} />
        <textarea value={researchQuestion} onChange={(event) => setResearchQuestion(event.target.value)} placeholder="What should Smara research?" rows={3} className="mt-2 w-full rounded-md px-2.5 py-2 text-[12px] outline-none resize-y" style={{ background: "var(--bg-base)", border: "1px solid var(--border-dim)", color: "var(--text-primary)" }} />
        <input value={researchSources} onChange={(event) => setResearchSources(event.target.value)} placeholder="Source URLs (optional, comma separated)" className="mt-2 w-full rounded-md px-2.5 py-2 text-[12px] outline-none" style={{ background: "var(--bg-base)", border: "1px solid var(--border-dim)", color: "var(--text-primary)" }} />
        <div className="mt-2 flex justify-end"><button disabled={creatingResearch} onClick={() => void createResearch()} className="px-3 py-1.5 rounded-md text-[11px]" style={{ background: "var(--accent)", color: "#07111f", opacity: creatingResearch ? 0.6 : 1 }}>{creatingResearch ? "Creating…" : "Create research task"}</button></div>
      </div>}
      <div className="grid grid-cols-1 md:grid-cols-[minmax(12rem,0.7fr)_minmax(0,1.5fr)] gap-3 min-h-0 min-w-0 flex-1">
        <div className="flex min-h-0 min-w-0 flex-col gap-1.5 overflow-y-auto overscroll-contain pr-1 scrollbar-thin">
          {tasks.map((task) => (
            <button key={task.id} onClick={() => setSelectedId(task.id)} className="text-left px-3 py-2.5 rounded-lg" style={{ background: selectedId === task.id ? "var(--accent-soft)" : "var(--bg-card)", border: `1px solid ${selectedId === task.id ? "var(--accent)" : "var(--border-dim)"}` }}>
              <div className="flex items-center gap-2"><span className="w-2 h-2 rounded-full" style={{ background: statusColor[task.status] || "var(--text-muted)" }} /><span className="text-[12px] truncate" style={{ color: "var(--text-primary)" }}>{task.title}</span></div>
              <p className="text-[10px] mt-1 truncate" style={{ color: "var(--text-muted)" }}>{task.status.replace("_", " ")}</p>
            </button>
          ))}
          {!tasks.length && <p className="text-[12px] px-2 py-4" style={{ color: "var(--text-muted)" }}>Start work from chat, then track it here.</p>}
        </div>
        {!selected ? <div className="rounded-xl flex items-center justify-center" style={{ border: "1px dashed var(--border-default)", color: "var(--text-muted)" }}>Select a task to inspect its plan and evidence.</div> : (
           <div className="min-h-0 min-w-0 rounded-xl p-4 overflow-y-auto overscroll-contain scrollbar-thin" style={{ background: "var(--bg-card)", border: "1px solid var(--border-dim)" }}>
            <div className="flex items-start justify-between gap-3"><div><h2 className="text-[16px] font-semibold" style={{ color: "var(--text-primary)" }}>{selected.title}</h2><p className="text-[12px] mt-1" style={{ color: "var(--text-secondary)" }}>{selected.objective}</p></div><span className="text-[10px] uppercase tracking-wider" style={{ color: statusColor[selected.status] }}>{selected.status.replace("_", " ")}</span></div>
            {selected.status === "waiting_approval" && <div className="mt-4 p-3 rounded-lg flex items-center justify-between gap-2" style={{ background: "rgba(245,158,11,.08)", border: "1px solid rgba(245,158,11,.3)" }}><span className="text-[12px]" style={{ color: "#fbbf24" }}>This task is waiting for your approval.</span><div className="flex gap-2"><button disabled={busy} onClick={() => void decide(true)} className="px-2.5 py-1.5 rounded-md text-[11px]" style={{ background: "#34d399", color: "#06251a" }}><Check size={12} className="inline mr-1" />Approve</button><button disabled={busy} onClick={() => void decide(false)} className="px-2.5 py-1.5 rounded-md text-[11px]" style={{ background: "transparent", color: "#fca5a5", border: "1px solid rgba(248,113,113,.35)" }}><X size={12} className="inline mr-1" />Deny</button></div></div>}
            {!["completed", "failed", "cancelled"].includes(selected.status) && selected.status !== "waiting_approval" && <button disabled={busy} onClick={() => void cancel()} className="mt-3 px-2.5 py-1.5 rounded-md text-[11px]" style={{ color: "#fca5a5", border: "1px solid rgba(248,113,113,.35)" }}><Square size={11} className="inline mr-1" />Cancel task</button>}
            {selected.status === "failed" && <button disabled={busy} onClick={() => void retry()} className="mt-3 px-2.5 py-1.5 rounded-md text-[11px]" style={{ color: "var(--accent)", border: "1px solid var(--accent)" }}><RefreshCw size={11} className="inline mr-1" />Retry task</button>}
            <Section title="Plan"><div className="flex flex-col gap-1.5">{steps.map((step) => <div key={step.id} className="flex items-center gap-2 text-[11px]"><span style={{ color: statusColor[step.status] || "var(--text-muted)" }}>●</span><span style={{ color: "var(--text-secondary)" }}>{step.name}</span>{step.stage && <span className="rounded px-1.5 py-0.5 uppercase tracking-wide" style={{ color: "var(--accent)", background: "var(--accent-soft)" }}>{step.stage}</span>}<span className="ml-auto" style={{ color: "var(--text-muted)" }}>{step.status}{step.attempt > 1 ? ` · attempt ${step.attempt}` : ""}</span></div>)}</div></Section>
            {(() => { const job = workspaceJobFromSteps(steps); if (!job) return null; const checks = Array.isArray(job.acceptance_checks) ? job.acceptance_checks : []; const budgets = job.budgets || {}; return <Section title="Workspace run"><div className="rounded-lg p-3 text-[11px]" style={{ background: "var(--bg-base)", border: "1px solid var(--border-dim)" }}><div className="grid grid-cols-2 gap-x-3 gap-y-2"><span style={{ color: "var(--text-muted)" }}>Scope</span><span className="text-right truncate" style={{ color: "var(--text-secondary)" }}>{job.workspace_root || "approved workspace"}</span><span style={{ color: "var(--text-muted)" }}>Isolation</span><span className="text-right" style={{ color: "var(--text-secondary)" }}>{job.isolation || "none"}</span><span style={{ color: "var(--text-muted)" }}>Repair budget</span><span className="text-right" style={{ color: "var(--text-secondary)" }}>{budgets.max_repair_attempts ?? 0} retries</span></div>{checks.length > 0 && <div className="mt-3 pt-2" style={{ borderTop: "1px solid var(--border-dim)" }}><p className="mb-1 uppercase tracking-wider text-[10px]" style={{ color: "var(--text-muted)" }}>Acceptance checks</p>{checks.map((check, index) => <div key={`${check}-${index}`} className="flex gap-2 mt-1" style={{ color: "var(--text-secondary)" }}><span style={{ color: selected.status === "completed" ? "#34d399" : "#f59e0b" }}>{selected.status === "completed" ? "✓" : "·"}</span><span>{check}</span></div>)}</div>}</div></Section>; })()}
            {result && <Section title="Result"><div className="rounded-lg p-3 text-[12px] leading-6 whitespace-pre-wrap break-words" style={{ background: "var(--bg-base)", border: "1px solid var(--border-dim)", color: "var(--text-primary)" }}>{result}</div></Section>}
            {workspaceStageResults(artifacts).length > 0 && <Section title="Stage proof"><div className="flex flex-col gap-2">{workspaceStageResults(artifacts).map((stage, index) => <div className="rounded-lg p-2 text-[11px]" key={`${String(stage.stage)}-${index}`} style={{ background: "var(--bg-base)", border: "1px solid var(--border-dim)" }}><div className="flex items-center gap-2"><span className="uppercase tracking-wider" style={{ color: "var(--accent)" }}>{String(stage.stage || "stage")}</span><span style={{ color: stage.status === "completed" ? "#34d399" : "#f59e0b" }}>{String(stage.status || "pending")}</span></div><p className="mt-1" style={{ color: "var(--text-secondary)" }}>{String(stage.summary || "Bounded stage result")}</p>{Array.isArray(stage.acceptance) && <div className="mt-1 flex flex-col gap-1" style={{ color: "var(--text-muted)" }}>{(stage.acceptance as unknown[]).slice(0, 12).map((check, checkIndex) => <span key={checkIndex}>· {typeof check === "object" && check && "status" in check ? `${String((check as Record<string, unknown>).status)} — ${String((check as Record<string, unknown>).check || "acceptance check")}` : String(check)}</span>)}</div>}</div>)}</div></Section>}
            {selected.status === "completed" && !result && <Section title="Result"><span className="text-[11px]" style={{ color: "var(--text-muted)" }}>The task completed without a textual result.</span></Section>}
            <Section title="Activity"><div className="flex flex-col gap-1.5">{(() => { const activity = activityEvents(events); return <>{activity.visible.map((event) => <div key={event.id} className="text-[11px]" style={{ color: "var(--text-secondary)" }}><span style={{ color: "var(--text-muted)" }}>{new Date(event.created_at).toLocaleTimeString()}</span> · {eventLabel(event)}</div>)}{activity.collapsed > 0 && <span className="text-[10px]" style={{ color: "var(--text-muted)" }}>{activity.collapsed} progress update{activity.collapsed === 1 ? "" : "s"} collapsed</span>}{!events.length && <span className="text-[11px]" style={{ color: "var(--text-muted)" }}>No events yet.</span>}</>; })()}</div></Section>
            {evidence.length > 0 && <Section title={`Evidence (${evidence.length})`}><div className="flex flex-col gap-2">{evidence.map((item) => <details key={item.id} className="rounded-lg p-2" style={{ background: "var(--bg-base)", border: "1px solid var(--border-dim)" }}><summary className="cursor-pointer text-[11px]" style={{ color: "var(--accent)" }}><FileText size={12} className="inline mr-1" />{item.citation_label || item.title || item.url}<span className="ml-2" style={{ color: "var(--text-muted)" }}>{item.status}{item.confidence != null ? ` · ${Math.round(item.confidence * 100)}% confidence` : ""}</span></summary><a href={item.url} target="_blank" rel="noreferrer" className="block mt-2 text-[10px] break-all" style={{ color: "var(--accent)" }}>{item.url}</a>{item.excerpt && <p className="mt-2 text-[11px] leading-5" style={{ color: "var(--text-secondary)" }}>{item.excerpt}</p>}{item.verification_notes && <p className="mt-1 text-[10px]" style={{ color: "var(--text-muted)" }}>{item.verification_notes}</p>}</details>)}</div></Section>}
            {artifacts.length > 0 && <Section title={`Artifacts (${artifacts.length})`}><div className="flex flex-col gap-2">{artifacts.map((artifact) => { const structured = parseArtifact(artifact.content); const preview = structured?.preview && typeof structured.preview === "object" ? structured.preview as Record<string, unknown> : null; const changed = Array.isArray(structured?.changed_files) ? structured.changed_files : []; const diff = typeof structured?.diff === "string" ? structured.diff : (typeof preview?.diff === "string" ? preview.diff : null); return <details key={artifact.id} className="rounded-lg p-2" style={{ background: "var(--bg-base)", border: "1px solid var(--border-dim)" }}><summary className="cursor-pointer text-[11px]" style={{ color: "var(--text-secondary)" }}><FileText size={12} className="inline mr-1" />{artifact.name} <span style={{ color: "var(--text-muted)" }}>({artifact.kind})</span></summary>{artifact.sha256 && <p className="mt-2 text-[10px] break-all" style={{ color: "var(--text-muted)" }}>SHA-256: {artifact.sha256}</p>}{structured ? <div className="mt-2 flex flex-col gap-2 text-[11px]" style={{ color: "var(--text-secondary)" }}>{typeof structured.operation === "string" && <p><span style={{ color: "var(--text-muted)" }}>Operation:</span> {structured.operation}</p>}{typeof structured.path === "string" && <p><span style={{ color: "var(--text-muted)" }}>Path:</span> {structured.path}</p>}{changed.length > 0 && <p><span style={{ color: "var(--text-muted)" }}>Changed files:</span> {changed.map(String).join(", ")}</p>}{diff && <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-md p-2" style={{ background: "var(--bg-card)", color: "var(--text-secondary)" }}>{diff}</pre>}{typeof structured.output === "string" && <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-words rounded-md p-2" style={{ background: "var(--bg-card)", color: "var(--text-secondary)" }}>{structured.output}</pre>}</div> : artifact.content ? <pre className="mt-2 max-h-80 overflow-auto whitespace-pre-wrap break-words text-[11px] leading-5" style={{ color: "var(--text-secondary)" }}>{artifact.content}</pre> : <p className="mt-2 text-[10px]" style={{ color: "var(--text-muted)" }}>Artifact content is stored by Smara but is not available inline.</p>}</details>; })}</div></Section>}
            {error && <p className="mt-3 text-[11px] flex items-center gap-1" style={{ color: "#f87171" }}><CircleAlert size={12} />{error}</p>}
          </div>
        )}
      </div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return <section className="mt-4 pt-3" style={{ borderTop: "1px solid var(--border-dim)" }}><h3 className="text-[10px] uppercase tracking-wider mb-2" style={{ color: "var(--text-muted)" }}>{title}</h3>{children}</section>;
}
