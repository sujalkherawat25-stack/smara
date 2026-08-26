import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { Check, CircleAlert, FileText, RefreshCw, Square, X } from "lucide-react";
import {
  cancelSmaraTask,
  createSmaraTask,
  createSmaraResearch,
  decideSmaraTask,
  getSmaraArtifacts,
  getSmaraEvidence,
  getSmaraEvents,
  getSmaraSteps,
  listSmaraTasks,
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

export default function SmaraWorkPanel() {
  const [tasks, setTasks] = useState<SmaraTask[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [steps, setSteps] = useState<SmaraStep[]>([]);
  const [events, setEvents] = useState<SmaraEvent[]>([]);
  const [evidence, setEvidence] = useState<SmaraEvidence[]>([]);
  const [artifacts, setArtifacts] = useState<SmaraArtifact[]>([]);
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
      const [nextSteps, nextEvents, nextEvidence, nextArtifacts] = await Promise.all([
        getSmaraSteps(taskId), getSmaraEvents(taskId), getSmaraEvidence(taskId).catch(() => []), getSmaraArtifacts(taskId),
      ]);
      setSteps(nextSteps); setEvents(nextEvents); setEvidence(nextEvidence); setArtifacts(nextArtifacts);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not load task details.");
    }
  }, []);

  useEffect(() => { void refresh(false); }, [refresh]);
  useEffect(() => {
    if (!selectedId) { setSteps([]); setEvents([]); setEvidence([]); setArtifacts([]); return; }
    void loadDetails(selectedId);
    const active = selected?.status === "queued" || selected?.status === "running" || selected?.status === "waiting_approval";
    if (!active) return;
    const controller = new AbortController();
    // The stream carries durable events without polling. A low-frequency
    // refresh repairs any missed event after a proxy reconnect.
    void streamSmaraEvents(selectedId, controller.signal, () => { void loadDetails(selectedId); void refresh(); })
      .catch(() => { if (!controller.signal.aborted) void loadDetails(selectedId); });
    const timer = window.setInterval(() => { void refresh(); }, 15_000);
    return () => { controller.abort(); window.clearInterval(timer); };
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
    <div className="flex flex-col gap-3 h-full min-h-0">
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
      <div className="grid grid-cols-1 md:grid-cols-[minmax(12rem,0.7fr)_minmax(0,1.5fr)] gap-3 min-h-0 flex-1">
        <div className="flex flex-col gap-1.5 overflow-y-auto pr-1">
          {tasks.map((task) => (
            <button key={task.id} onClick={() => setSelectedId(task.id)} className="text-left px-3 py-2.5 rounded-lg" style={{ background: selectedId === task.id ? "var(--accent-soft)" : "var(--bg-card)", border: `1px solid ${selectedId === task.id ? "var(--accent)" : "var(--border-dim)"}` }}>
              <div className="flex items-center gap-2"><span className="w-2 h-2 rounded-full" style={{ background: statusColor[task.status] || "var(--text-muted)" }} /><span className="text-[12px] truncate" style={{ color: "var(--text-primary)" }}>{task.title}</span></div>
              <p className="text-[10px] mt-1 truncate" style={{ color: "var(--text-muted)" }}>{task.status.replace("_", " ")}</p>
            </button>
          ))}
          {!tasks.length && <p className="text-[12px] px-2 py-4" style={{ color: "var(--text-muted)" }}>Start work from chat, then track it here.</p>}
        </div>
        {!selected ? <div className="rounded-xl flex items-center justify-center" style={{ border: "1px dashed var(--border-default)", color: "var(--text-muted)" }}>Select a task to inspect its plan and evidence.</div> : (
          <div className="rounded-xl p-4 overflow-y-auto" style={{ background: "var(--bg-card)", border: "1px solid var(--border-dim)" }}>
            <div className="flex items-start justify-between gap-3"><div><h2 className="text-[16px] font-semibold" style={{ color: "var(--text-primary)" }}>{selected.title}</h2><p className="text-[12px] mt-1" style={{ color: "var(--text-secondary)" }}>{selected.objective}</p></div><span className="text-[10px] uppercase tracking-wider" style={{ color: statusColor[selected.status] }}>{selected.status.replace("_", " ")}</span></div>
            {selected.status === "waiting_approval" && <div className="mt-4 p-3 rounded-lg flex items-center justify-between gap-2" style={{ background: "rgba(245,158,11,.08)", border: "1px solid rgba(245,158,11,.3)" }}><span className="text-[12px]" style={{ color: "#fbbf24" }}>This task is waiting for your approval.</span><div className="flex gap-2"><button disabled={busy} onClick={() => void decide(true)} className="px-2.5 py-1.5 rounded-md text-[11px]" style={{ background: "#34d399", color: "#06251a" }}><Check size={12} className="inline mr-1" />Approve</button><button disabled={busy} onClick={() => void decide(false)} className="px-2.5 py-1.5 rounded-md text-[11px]" style={{ background: "transparent", color: "#fca5a5", border: "1px solid rgba(248,113,113,.35)" }}><X size={12} className="inline mr-1" />Deny</button></div></div>}
            {!["completed", "failed", "cancelled"].includes(selected.status) && selected.status !== "waiting_approval" && <button disabled={busy} onClick={() => void cancel()} className="mt-3 px-2.5 py-1.5 rounded-md text-[11px]" style={{ color: "#fca5a5", border: "1px solid rgba(248,113,113,.35)" }}><Square size={11} className="inline mr-1" />Cancel task</button>}
            <Section title="Plan"><div className="flex flex-col gap-1.5">{steps.map((step) => <div key={step.id} className="flex items-center gap-2 text-[11px]"><span style={{ color: statusColor[step.status] || "var(--text-muted)" }}>●</span><span style={{ color: "var(--text-secondary)" }}>{step.name}</span><span className="ml-auto" style={{ color: "var(--text-muted)" }}>{step.status}</span></div>)}</div></Section>
            <Section title="Activity"><div className="flex flex-col gap-1.5">{events.slice(-12).map((event) => <div key={event.id} className="text-[11px]" style={{ color: "var(--text-secondary)" }}><span style={{ color: "var(--text-muted)" }}>{new Date(event.created_at).toLocaleTimeString()}</span> · {event.message || event.event_type}</div>)}{!events.length && <span className="text-[11px]" style={{ color: "var(--text-muted)" }}>No events yet.</span>}</div></Section>
            {evidence.length > 0 && <Section title={`Evidence (${evidence.length})`}><div className="flex flex-col gap-2">{evidence.map((item) => <a key={item.id} href={item.url} target="_blank" rel="noreferrer" className="text-[11px]" style={{ color: "var(--accent)" }}><FileText size={12} className="inline mr-1" />{item.citation_label || item.title || item.url}<span className="block ml-4" style={{ color: "var(--text-muted)" }}>{item.status}{item.confidence != null ? ` · ${Math.round(item.confidence * 100)}% confidence` : ""}</span></a>)}</div></Section>}
            {artifacts.length > 0 && <Section title={`Artifacts (${artifacts.length})`}><div className="flex flex-col gap-1">{artifacts.map((artifact) => <div key={artifact.id} className="text-[11px]" style={{ color: "var(--text-secondary)" }}><FileText size={12} className="inline mr-1" />{artifact.name} <span style={{ color: "var(--text-muted)" }}>({artifact.kind})</span></div>)}</div></Section>}
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
