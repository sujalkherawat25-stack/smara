import { smaraFetch } from "@/lib/smaraGateway";

export interface SmaraTask {
  id: string;
  account_id: string;
  workspace_id: string;
  title: string;
  objective: string;
  status: "queued" | "running" | "waiting_approval" | "cancelling" | "completed" | "failed" | "cancelled";
  requires_approval: boolean;
  created_at: string;
  updated_at: string;
}

export interface SmaraStep {
  id: string;
  task_id: string;
  name: string;
  status: string;
  executor_kind: string;
  required_capability: string | null;
  attempt: number;
  error: string | null;
}

export interface SmaraEvent {
  id: string;
  task_id: string;
  event_type: string;
  message: string;
  created_at: string;
}

export interface SmaraEvidence {
  id: string;
  url: string;
  title: string | null;
  status: string;
  excerpt: string | null;
  citation_label: string | null;
  confidence: number | null;
  verification_notes: string | null;
}

export interface SmaraArtifact {
  id: string;
  kind: string;
  name: string;
  uri: string;
  content: string | null;
  created_at: string;
}

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await smaraFetch(path, init);
  if (!response.ok) {
    const detail = await response.json().catch(() => ({ detail: response.statusText })) as { detail?: string };
    throw new Error(detail.detail || `Smara request failed (${response.status})`);
  }
  return response.json() as Promise<T>;
}

export function listSmaraTasks(): Promise<SmaraTask[]> {
  return json<SmaraTask[]>("/v1/tasks");
}

export function getSmaraTask(taskId: string): Promise<SmaraTask> {
  return json<SmaraTask>(`/v1/tasks/${encodeURIComponent(taskId)}`);
}

export async function getSmaraSteps(taskId: string): Promise<SmaraStep[]> {
  const result = await json<{ steps: SmaraStep[] }>(`/v1/tasks/${encodeURIComponent(taskId)}/steps`);
  return result.steps;
}

export async function getSmaraEvents(taskId: string): Promise<SmaraEvent[]> {
  const result = await json<{ events: SmaraEvent[] }>(`/v1/tasks/${encodeURIComponent(taskId)}/events`);
  return result.events;
}

export function getSmaraEvidence(taskId: string): Promise<SmaraEvidence[]> {
  return json<SmaraEvidence[]>(`/v1/research/${encodeURIComponent(taskId)}/evidence`);
}

export function getSmaraArtifacts(taskId: string): Promise<SmaraArtifact[]> {
  return json<SmaraArtifact[]>(`/v1/tasks/${encodeURIComponent(taskId)}/artifacts`);
}

export function decideSmaraTask(taskId: string, approved: boolean): Promise<SmaraTask> {
  return json<SmaraTask>(`/v1/tasks/${encodeURIComponent(taskId)}/approval`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ approved, note: "Decided from Smara Web" }),
  });
}

export function cancelSmaraTask(taskId: string): Promise<SmaraTask> {
  return json<SmaraTask>(`/v1/tasks/${encodeURIComponent(taskId)}/cancel`, { method: "POST" });
}
