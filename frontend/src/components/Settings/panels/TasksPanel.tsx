/**
 * Settings/panels/TasksPanel.tsx — scheduled tasks list + history.
 *
 * Tasks are created via chat (the agent's schedule_task tool). This panel
 * shows the list, allows pause/resume, delete, and expands to show per-task
 * run history (last 10 runs with status badges + output excerpts).
 */

import { useState, useEffect } from "react";
import { Trash2, Calendar, Power, AlertCircle } from "lucide-react";
import { useTasksStore } from "@/stores/tasksStore";
import { fetchTaskRuns, type ScheduledTask, type TaskRun } from "@/lib/tasks";

export default function TasksPanel() {
  const items = useTasksStore((s) => s.items);
  const loaded = useTasksStore((s) => s.loaded);
  const refresh = useTasksStore((s) => s.refresh);
  const toggle = useTasksStore((s) => s.toggle);
  const remove = useTasksStore((s) => s.remove);

  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (!loaded) {
    return <p className="text-[12px]" style={{ color: "var(--text-muted)" }}>Loading tasks…</p>;
  }

  return (
    <div className="flex flex-col gap-3 max-w-2xl">
      <div className="flex items-center justify-between">
        <p className="text-[13px]" style={{ color: "var(--text-secondary)" }}>
          {items.length === 0
            ? "No scheduled tasks."
            : `${items.length} scheduled task${items.length === 1 ? "" : "s"}`}
        </p>
        <button
          onClick={refresh}
          className="text-[11px] px-2 py-1 rounded-md"
          style={{ color: "var(--text-muted)", border: "1px solid var(--border-dim)" }}
        >
          Refresh
        </button>
      </div>

      {items.length === 0 && (
        <div
          className="px-4 py-5 rounded-xl text-center"
          style={{ background: "var(--bg-card)", border: "1px dashed var(--border-default)" }}
        >
          <p className="text-[12px]" style={{ color: "var(--text-secondary)" }}>
            Ask the agent — e.g.
          </p>
          <p className="text-[12px] mt-1 font-mono" style={{ color: "var(--text-primary)" }}>
            "every morning at 9am give me the latest AI news"
          </p>
        </div>
      )}

      {items.length > 0 && (
        <div className="flex flex-col gap-2 max-h-[520px] overflow-y-auto pr-1">
          {items.map((t) => (
            <TaskRow
              key={t.id}
              task={t}
              expanded={expandedId === t.id}
              busy={busyId === t.id}
              confirmDelete={confirmDeleteId === t.id}
              onExpand={() => setExpandedId(expandedId === t.id ? null : t.id)}
              onToggle={async () => {
                setBusyId(t.id);
                setError(null);
                const ok = await toggle(t.id, !t.enabled);
                if (!ok) setError("Couldn't update the task.");
                setBusyId(null);
              }}
              onDelete={async () => {
                if (confirmDeleteId !== t.id) { setConfirmDeleteId(t.id); return; }
                setBusyId(t.id);
                setError(null);
                const ok = await remove(t.id);
                if (!ok) setError("Couldn't delete the task.");
                setBusyId(null);
                setConfirmDeleteId(null);
              }}
            />
          ))}
        </div>
      )}

      {error && <p className="text-[11px]" style={{ color: "#ef4444" }}>{error}</p>}
    </div>
  );
}

function TaskRow({
  task, expanded, busy, confirmDelete, onExpand, onToggle, onDelete,
}: {
  task: ScheduledTask;
  expanded: boolean;
  busy: boolean;
  confirmDelete: boolean;
  onExpand: () => void;
  onToggle: () => Promise<void>;
  onDelete: () => Promise<void>;
}) {
  const isRecurring = !!task.cron_expr;
  const scheduleText = isRecurring
    ? `${prettyCron(task.cron_expr!)} (${task.timezone})`
    : task.fire_at
      ? `Once on ${new Date(task.fire_at).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })}`
      : "—";
  const nextText = task.next_run_at && task.enabled
    ? `next: ${new Date(task.next_run_at).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })}`
    : task.enabled ? "" : "paused";

  const lastBadge = task.last_status
    ? {
        ok: { label: "ok", color: "var(--accent2)", bg: "rgba(52,211,153,0.10)" },
        error: { label: "error", color: "#ef4444", bg: "rgba(239,68,68,0.10)" },
        no_channel: { label: "queued", color: "#f59e0b", bg: "rgba(245,158,11,0.10)" },
      }[task.last_status as "ok" | "error" | "no_channel"] || null
    : null;

  return (
    <div
      className="rounded-xl"
      style={{
        background: "var(--bg-card)",
        border: "1px solid var(--border-dim)",
        opacity: task.enabled ? 1 : 0.6,
      }}
    >
      <div className="px-3 py-2.5 flex items-start gap-3">
        <button
          onClick={onExpand}
          className="w-7 h-7 rounded-md flex items-center justify-center shrink-0 mt-0.5"
          style={{ background: "var(--accent-soft)" }}
          title="View run history"
        >
          <Calendar size={12} style={{ color: "var(--accent)" }} />
        </button>

        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <p
              className="text-[13px] font-medium truncate"
              style={{ color: "var(--text-primary)" }}
              title={task.label}
            >
              {task.label}
            </p>
            {lastBadge && (
              <span
                className="text-[9px] px-1.5 py-0.5 rounded-full uppercase tracking-wider"
                style={{ background: lastBadge.bg, color: lastBadge.color }}
              >
                {lastBadge.label}
              </span>
            )}
          </div>
          <p
            className="text-[11px] mt-0.5 truncate"
            style={{ color: "var(--text-muted)" }}
            title={task.prompt}
          >
            {task.prompt}
          </p>
          <p className="text-[11px] mt-1" style={{ color: "var(--text-secondary)" }}>
            {scheduleText}
            {nextText && (<>{" · "}<span style={{ color: "var(--text-muted)" }}>{nextText}</span></>)}
          </p>
        </div>

        <div className="flex flex-col gap-1 shrink-0">
          <button
            onClick={onToggle}
            disabled={busy}
            className="px-2 py-1 rounded-md text-[11px] transition-all flex items-center gap-1"
            style={{
              background: task.enabled ? "rgba(52,211,153,0.10)" : "transparent",
              color: task.enabled ? "var(--accent2)" : "var(--text-muted)",
              border: `1px solid ${task.enabled ? "rgba(52,211,153,0.30)" : "var(--border-dim)"}`,
            }}
            title={task.enabled ? "Pause this task" : "Resume this task"}
          >
            <Power size={10} />
            {task.enabled ? "on" : "off"}
          </button>
          <button
            onClick={onDelete}
            disabled={busy}
            className="px-2 py-1 rounded-md text-[11px] transition-all"
            style={{
              background: confirmDelete ? "rgba(239,68,68,0.10)" : "transparent",
              color: confirmDelete ? "#ef4444" : "var(--text-muted)",
              border: `1px solid ${confirmDelete ? "rgba(239,68,68,0.30)" : "var(--border-dim)"}`,
            }}
            title={confirmDelete ? "Click to confirm" : "Delete task"}
          >
            {confirmDelete ? "sure?" : <Trash2 size={10} />}
          </button>
        </div>
      </div>

      {expanded && <TaskRunHistory taskId={task.id} />}
    </div>
  );
}

function TaskRunHistory({ taskId }: { taskId: string }) {
  const [runs, setRuns] = useState<TaskRun[] | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchTaskRuns(taskId, 10).then((rs) => {
      if (!cancelled) { setRuns(rs); setLoading(false); }
    });
    return () => { cancelled = true; };
  }, [taskId]);

  return (
    <div className="px-3 pb-3" style={{ borderTop: "1px solid var(--border-dim)" }}>
      <p className="text-[10px] font-semibold uppercase tracking-wider mt-2.5 mb-1.5" style={{ color: "var(--text-muted)" }}>
        Recent runs
      </p>
      {loading && <p className="text-[11px]" style={{ color: "var(--text-muted)" }}>Loading…</p>}
      {!loading && runs && runs.length === 0 && (
        <p className="text-[11px]" style={{ color: "var(--text-muted)" }}>No runs yet.</p>
      )}
      {!loading && runs && runs.length > 0 && (
        <div className="flex flex-col gap-1.5">
          {runs.map((r) => {
            const status = r.status as "ok" | "error" | "no_channel";
            const statusColor =
              status === "ok" ? "var(--accent2)" :
              status === "no_channel" ? "#f59e0b" : "#ef4444";
            return (
              <div
                key={r.id}
                className="text-[11px] px-2.5 py-1.5 rounded-md"
                style={{ background: "var(--bg-base)", border: "1px solid var(--border-dim)" }}
              >
                <div className="flex items-center gap-2">
                  <span style={{ color: statusColor }}>●</span>
                  <span style={{ color: "var(--text-secondary)" }}>
                    {new Date(r.run_at).toLocaleString(undefined, { dateStyle: "short", timeStyle: "short" })}
                  </span>
                  {r.duration_ms != null && (
                    <span style={{ color: "var(--text-muted)" }}>· {r.duration_ms}ms</span>
                  )}
                </div>
                {r.output_excerpt && (
                  <p className="mt-1 line-clamp-2" style={{ color: "var(--text-primary)", whiteSpace: "pre-wrap" }}>
                    {r.output_excerpt}
                  </p>
                )}
                {r.error && (
                  <p className="mt-1 flex items-center gap-1" style={{ color: "#ef4444" }}>
                    <AlertCircle size={10} />
                    {r.error}
                  </p>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function prettyCron(expr: string): string {
  const parts = expr.trim().split(/\s+/);
  if (parts.length !== 5) return expr;
  const [m, h, dom, mon, dow] = parts;
  const time = (h !== "*" && m !== "*") ? formatHourMin(h, m) : null;
  if (dom === "*" && mon === "*" && dow === "*" && time) return `Every day at ${time}`;
  if (dom === "*" && mon === "*" && dow !== "*" && time) {
    const days = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
    const idx = parseInt(dow, 10);
    if (!isNaN(idx) && idx >= 0 && idx <= 7) return `Every ${days[idx % 7]} at ${time}`;
  }
  if (dom !== "*" && mon === "*" && dow === "*" && time) {
    return `Every month on day ${dom} at ${time}`;
  }
  return `cron: ${expr}`;
}

function formatHourMin(h: string, m: string): string {
  const hh = parseInt(h, 10);
  const mm = parseInt(m, 10);
  if (isNaN(hh) || isNaN(mm)) return `${h}:${m}`;
  const ampm = hh >= 12 ? "PM" : "AM";
  const h12 = hh % 12 === 0 ? 12 : hh % 12;
  return `${h12}:${mm.toString().padStart(2, "0")} ${ampm}`;
}
