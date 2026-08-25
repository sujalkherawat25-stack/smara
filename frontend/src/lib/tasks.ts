/**
 * lib/tasks.ts — frontend API helpers for F5.1+ scheduled tasks.
 *
 * Tasks are richer than reminders: they carry a prompt (not a fixed string),
 * have either a one-off `fire_at` or recurring `cron_expr`, and accumulate
 * a run history.
 */

export interface ScheduledTask {
  id: string;
  label: string;
  prompt: string;
  fire_at: string | null;
  cron_expr: string | null;
  timezone: string;
  channel: string | null;
  enabled: boolean;
  next_run_at: string | null;
  last_run_at: string | null;
  last_status: string | null;     // 'ok' | 'error' | 'no_channel' | null
  created_at: string;
}

export interface TaskRun {
  id: number;
  run_at: string;
  status: string;
  duration_ms: number | null;
  output_excerpt: string | null;
  conversation_id: string | null;
  error: string | null;
}

const COMMON: RequestInit = {
  credentials: "include",
  headers: { "Content-Type": "application/json" },
};

import { smaraFetch, smaraModeEnabled } from "@/lib/smaraGateway";

interface SmaraTask {
  id: string;
  title: string;
  objective: string;
  status: string;
  created_at: string;
  updated_at: string;
}

function fromSmaraTask(task: SmaraTask): ScheduledTask {
  return {
    id: task.id,
    label: task.title,
    prompt: task.objective,
    fire_at: null,
    cron_expr: null,
    timezone: "UTC",
    channel: null,
    enabled: !["cancelled", "completed", "failed"].includes(task.status),
    next_run_at: null,
    last_run_at: task.updated_at,
    last_status: task.status,
    created_at: task.created_at,
  };
}

export async function fetchTasks(): Promise<ScheduledTask[]> {
  let r: Response;
  try {
    r = await smaraFetch(smaraModeEnabled() ? "/v1/tasks" : "/v1/memento/tasks", { ...COMMON, method: "GET" });
  } catch (e) {
    console.error("[tasks] network error:", e);
    return [];
  }
  if (!r.ok) {
    if (r.status !== 401 && r.status !== 503) {
      console.error("[tasks] HTTP", r.status, await r.text().catch(() => ""));
    }
    return [];
  }
  const payload = await r.json();
  return smaraModeEnabled() ? (payload as SmaraTask[]).map(fromSmaraTask) : payload as ScheduledTask[];
}

export async function setTaskEnabled(taskId: string, enabled: boolean): Promise<ScheduledTask | null> {
  if (smaraModeEnabled()) return null;
  const r = await smaraFetch(`/v1/memento/tasks/${encodeURIComponent(taskId)}`, {
    ...COMMON,
    method: "PATCH",
    body: JSON.stringify({ enabled }),
  });
  if (!r.ok) return null;
  return (await r.json()) as ScheduledTask;
}

export async function deleteTask(taskId: string): Promise<boolean> {
  if (smaraModeEnabled()) {
    const r = await smaraFetch(`/v1/tasks/${encodeURIComponent(taskId)}/cancel`, { ...COMMON, method: "POST" });
    return r.ok;
  }
  const r = await smaraFetch(`/v1/memento/tasks/${encodeURIComponent(taskId)}`, {
    ...COMMON,
    method: "DELETE",
  });
  return r.ok;
}

export async function fetchTaskRuns(taskId: string, limit = 20): Promise<TaskRun[]> {
  if (smaraModeEnabled()) return [];
  const r = await smaraFetch(
    `/v1/memento/tasks/${encodeURIComponent(taskId)}/runs?limit=${limit}`,
    { ...COMMON, method: "GET" },
  );
  if (!r.ok) return [];
  return (await r.json()) as TaskRun[];
}
