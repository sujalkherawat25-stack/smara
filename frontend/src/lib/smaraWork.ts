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
  /** Stored by the API as `type` and a JSON `payload`. */
  type?: string;
  payload?: string | Record<string, unknown>;
  /** Legacy aliases accepted while older deployments are being drained. */
  event_type?: string;
  message?: string;
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

export function createSmaraTask(input: {
  title: string;
  objective: string;
  workspace_id?: string;
  requires_approval?: boolean;
}): Promise<SmaraTask> {
  return json<SmaraTask>("/v1/tasks", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      ...input,
      workspace_id: input.workspace_id || "default",
      requires_approval: input.requires_approval ?? true,
    }),
  });
}

export function createSmaraResearch(input: {
  title: string;
  question: string;
  workspace_id?: string;
  sources?: string[];
}): Promise<SmaraTask> {
  return json<SmaraTask>("/v1/research", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...input, workspace_id: input.workspace_id || "default" }),
  });
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

/** Consume the durable task SSE stream until completion or cancellation. */
export async function streamSmaraEvents(
  taskId: string,
  signal: AbortSignal,
  onEvent: (event: SmaraEvent) => void,
): Promise<void> {
  const response = await smaraFetch(`/v1/tasks/${encodeURIComponent(taskId)}/events/stream`, {
    headers: { Accept: "text/event-stream" },
    signal,
  });
  if (!response.ok || !response.body) {
    throw new Error(`Task event stream failed (${response.status})`);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) return;
    buffer += decoder.decode(value, { stream: true });
    let boundary: number;
    while ((boundary = buffer.indexOf("\n\n")) >= 0) {
      const frame = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      const data = frame.split("\n").find((line) => line.startsWith("data:"));
      if (!data) continue;
      try { onEvent(JSON.parse(data.slice(5).trim()) as SmaraEvent); } catch { /* keep the stream alive */ }
    }
  }
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
