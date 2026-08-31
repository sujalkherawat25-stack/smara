import { smaraFetch } from "@/lib/smaraGateway";

export interface SmaraTask {
  id: string;
  account_id: string;
  workspace_id: string;
  title: string;
  objective: string;
  status: "queued" | "running" | "waiting_approval" | "cancelling" | "completed" | "failed" | "cancelled";
  requires_approval: boolean;
  approval_mode?: "hosted" | "desktop";
  result?: string | null;
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
  stage?: string;
  operation?: string;
  workspace_job?: {
    schema_version?: string;
    workspace_root?: string;
    objective?: string;
    acceptance_checks?: string[];
    allowed_capabilities?: string[];
    approval_policy?: string;
    isolation?: string;
    budgets?: { time_budget_seconds?: number; cost_budget_inr?: number; max_repair_attempts?: number };
  };
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
  sha256?: string | null;
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

export interface SmaraEventStreamOptions {
  /** Resume from the last durable event after a network/proxy reconnect. */
  lastEventId?: string;
  /** Called when a dropped stream is being re-established. */
  onReconnect?: (lastEventId: string | undefined, attempt: number) => void;
}

function waitForRetry(ms: number, signal: AbortSignal): Promise<void> {
  if (signal.aborted) return Promise.reject(new DOMException("Aborted", "AbortError"));
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(resolve, ms);
    signal.addEventListener("abort", () => {
      window.clearTimeout(timer);
      reject(new DOMException("Aborted", "AbortError"));
    }, { once: true });
  });
}

/**
 * Consume the durable task SSE stream until completion or cancellation.
 *
 * The API keeps a durable cursor, so a transient browser/proxy disconnect is
 * safe: reconnects send Last-Event-ID and only deliver events after that
 * cursor. Keep this helper independent of React so Desktop/Web can share the
 * same recovery semantics.
 */
export async function streamSmaraEvents(
  taskId: string,
  signal: AbortSignal,
  onEvent: (event: SmaraEvent) => void,
  options: SmaraEventStreamOptions = {},
): Promise<void> {
  let cursor = options.lastEventId;
  let reconnectAttempt = 0;
  const streamUrl = `/v1/tasks/${encodeURIComponent(taskId)}/events/stream`;

  while (!signal.aborted) {
    const headers = new Headers({ Accept: "text/event-stream" });
    if (cursor) headers.set("Last-Event-ID", cursor);
    let response: Response;
    try {
      response = await smaraFetch(streamUrl, { headers, signal });
    } catch (cause) {
      if (signal.aborted) return;
      reconnectAttempt += 1;
      options.onReconnect?.(cursor, reconnectAttempt);
      await waitForRetry(Math.min(5000, 250 * 2 ** Math.min(reconnectAttempt - 1, 4)), signal);
      continue;
    }
    if (!response.ok || !response.body) {
      // Authentication, ownership, and task-not-found errors are actionable;
      // retrying them forever would hide the real problem from the panel.
      throw new Error(`Task event stream failed (${response.status})`);
    }

    reconnectAttempt = 0;
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let frameEvent = "message";
    let frameId: string | undefined;
    let frameData: string[] = [];
    let terminal = false;

    const consumeFrame = (frame: string) => {
      for (const rawLine of frame.split(/\r?\n/)) {
        if (!rawLine || rawLine.startsWith(":")) continue;
        const separator = rawLine.indexOf(":");
        const field = separator >= 0 ? rawLine.slice(0, separator) : rawLine;
        const value = separator >= 0 ? rawLine.slice(separator + 1).trimStart() : "";
        if (field === "event") frameEvent = value;
        else if (field === "id") frameId = value;
        else if (field === "data") frameData.push(value);
      }
      if (frameId) cursor = frameId;
      if (frameData.length && frameEvent !== "done") {
        try { onEvent(JSON.parse(frameData.join("\n")) as SmaraEvent); } catch { /* ignore malformed frames */ }
      }
      terminal = frameEvent === "done";
      frameEvent = "message";
      frameId = undefined;
      frameData = [];
    };

    while (!signal.aborted) {
      const { done, value } = await reader.read();
      if (value) buffer += decoder.decode(value, { stream: !done });
      let boundary: number;
      while ((boundary = buffer.search(/\r?\n\r?\n/)) >= 0) {
        const match = buffer.slice(boundary).match(/^\r?\n\r?\n/);
        const separatorLength = match?.[0].length ?? 2;
        consumeFrame(buffer.slice(0, boundary));
        buffer = buffer.slice(boundary + separatorLength);
        if (terminal) return;
      }
      if (done) {
        if (buffer.trim()) consumeFrame(buffer);
        if (terminal || signal.aborted) return;
        // A closed stream without a `done` frame is a transient disconnect.
        reconnectAttempt += 1;
        options.onReconnect?.(cursor, reconnectAttempt);
        await waitForRetry(Math.min(5000, 250 * 2 ** Math.min(reconnectAttempt - 1, 4)), signal);
        break;
      }
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

export function retrySmaraTask(taskId: string): Promise<SmaraTask> {
  return json<SmaraTask>(`/v1/tasks/${encodeURIComponent(taskId)}/retry`, { method: "POST" });
}
