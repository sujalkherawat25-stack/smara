/** Presentation only; native Rust owns execution and permissions. */
export type ViewItem = { id: string; kind: string; text: string; status?: string; command?: string; cwd?: string; exitCode?: number; durationMs?: number };
export type NativeTurn = { id: string; status: string; items: Record<string, unknown>[] };
export type WorkerView = { id: string; status: string; cwd?: string; activity?: string; lastActivityAt?: number; observedItems?: number };
/** Unknown child state is not proof of completion or permission to unlock writes. */
export function workerOutstanding(worker: WorkerView): boolean {
  return !["completed", "failed", "shutdown", "notFound", "interrupted", "errored", "closed"].includes(worker.status);
}
export function displayRuntimeError(value: unknown): string {
  const message = String(value ?? "Native runtime error");
  if (/incomplete response returned,\s*reason:\s*max_output_tokens/i.test(message)) {
    return "The model reached its response-token limit before finishing this step. Incomplete tool calls were not executed. Use Continue to inspect completed work and resume; Smara will not replay the original actions.";
  }
  if (/context.{0,12}(length|window)|maximum context|too many tokens|prompt is too long|exceeds? the (model|context)|context_length_exceeded/i.test(message)) {
    return "This conversation filled the model's context window. Smara compacts older turns automatically, but this request was still too large. Use Continue to resume from the summary, or start a new chat; if your model has a larger window, set it under Settings → Models → Context window.";
  }
  return message;
}
export function workerSnapshot(workers: WorkerView[], id: string, turn: NativeTurn, now: number): WorkerView[] {
  return workers.map(worker => {
    if (worker.id !== id) return worker;
    // A summary request can race a newer turn/completed notification. Never
    // let that older in-progress snapshot resurrect a terminal worker state.
    if (["completed", "failed", "shutdown", "notFound", "interrupted"].includes(worker.status) && turn.status === "inProgress") return worker;
    const count = turn.items.length;
    const changed = worker.status !== turn.status || worker.observedItems !== count;
    return { ...worker, status: turn.status, observedItems: count,
      activity: `${count} recorded items`, lastActivityAt: changed ? now : worker.lastActivityAt };
  });
}
/** Show known child activity without leaking its transcript into the parent. */
export function workerActivity(workers: WorkerView[], message: { method?: string; params?: Record<string, unknown> }, now: number): WorkerView[] {
  const params = message.params ?? {};
  const id = params.threadId;
  if (!workers.some(worker => worker.id === id)) return workers;
  const item = params.item as Record<string, unknown> | undefined;
  const turn = params.turn as { status?: string } | undefined;
  let status: string | undefined;
  let activity: string | undefined;
  if (message.method === "turn/started") { status = "inProgress"; activity = "Working"; }
  else if (message.method === "turn/completed") { status = turn?.status; activity = "Turn finished"; }
  else if (message.method === "item/started" || message.method === "item/completed") {
    activity = `${String(item?.type ?? "Tool")} · ${message.method === "item/started" ? "running" : "finished"}`;
  } else if (message.method === "item/agentMessage/delta") activity = "Writing response";
  else if (message.method === "item/commandExecution/outputDelta") activity = "Command output received";
  else return workers;
  return workers.map(worker => worker.id === id ? { ...worker, status: status ?? worker.status, activity, lastActivityAt: now } : worker);
}
/** Only workers named by the selected parent's native collaboration items. */
export function mergeWorkers(workers: WorkerView[], item: Record<string, unknown>): WorkerView[] {
  if (item.type !== "collabAgentToolCall" || !Array.isArray(item.receiverThreadIds)) return workers;
  const states = item.agentsStates as Record<string, { status?: string }> | undefined;
  const result = [...workers];
  for (const id of item.receiverThreadIds) {
    if (typeof id !== "string" || !id) continue;
    const index = result.findIndex(worker => worker.id === id);
    const previous = index < 0 ? undefined : result[index];
    // Completing a spawn/wait tool is not evidence that a worker completed.
    const status = states?.[id]?.status ?? previous?.status ?? "status unknown";
    const next = { ...previous, id, status };
    if (index < 0) result.push(next); else result[index] = next;
  }
  return result;
}
export function threadLabel(thread: { name?: string | null; preview?: string }): string {
  const text = thread.name?.trim() || readablePreview(thread.preview?.trim() ?? "") || "New conversation";
  return text.replace(/\s+/g, " ").slice(0, 96);
}
/** Background tasks start their chat with a JSON brief; show its objective, never the raw JSON. */
function readablePreview(preview: string): string {
  if (!preview.startsWith("{")) return preview;
  const objective = /"objective"\s*:\s*"((?:[^"\\]|\\.)*)/.exec(preview)?.[1];
  if (objective) return "Task: " + objective.replace(/\\n/g, " ").replace(/\\"/g, '"');
  return "Background task";
}
const TOOL_TITLES: Record<string, string> = {
  team_list: "Looked at the team", team_hire: "Hired an employee", team_update: "Changed an employee", team_remember: "Saved a standing rule",
  team_delegate: "Handed a job to an employee", team_tasks: "Checked the team's work", team_routine: "Set up a routine",
  team_routines: "Checked routines", team_routine_pause: "Paused or resumed a routine", team_remove: "Removed an employee",
  computer_request_access: "Asked to use your apps", skill_list: "Checked skills", skill_read: "Followed a skill", skill_save: "Saved a skill", skill_forget: "Forgot a skill",
};
/** A tool call in the owner's words: "Hired an employee · Zoya", "Browser · navigate". */
export function toolTitle(tool: string, args: unknown): string {
  const params = args && typeof args === "object" ? args as Record<string, unknown> : {};
  const who = tool.startsWith("skill_") && typeof params.name === "string" ? params.name
    : typeof params.name === "string" ? params.name : typeof params.employee === "string" ? params.employee : "";
  if (TOOL_TITLES[tool]) return who ? `${TOOL_TITLES[tool]} · ${who}` : TOOL_TITLES[tool];
  const [group, ...rest] = tool.split("_");
  return rest.length ? `${group.charAt(0).toUpperCase()}${group.slice(1)} · ${rest.join(" ")}` : tool || "Tool";
}
function toolResultText(result: unknown): string {
  const content = result && typeof result === "object" ? (result as { content?: unknown }).content : undefined;
  if (!Array.isArray(content)) return "";
  return content.map(part => part && typeof part === "object" && typeof (part as { text?: unknown }).text === "string" ? (part as { text: string }).text : "").join("\n").slice(0, 4000);
}
export function answerKey(requestId: number | string, questionId: string): string {
  return JSON.stringify([requestId, questionId]);
}
/** Requests are connection-wide (including workers); transcript events are not. */
export function routeMessage(message: { id?: number | string; method?: string; params?: Record<string, unknown> }, selectedThread: string): "request" | "resolved" | "notification" | "ignore" {
  if (message.id !== undefined && message.method) return "request";
  if (message.method === "serverRequest/resolved") return "resolved";
  const thread = message.params?.threadId;
  return thread && thread !== selectedThread ? "ignore" : "notification";
}
export function displayItem(item: Record<string, unknown>): ViewItem {
  const kind = String(item.type ?? "tool");
  const content = item.content;
  if (kind === "commandExecution") return {
    id: String(item.id), kind, text: typeof item.aggregatedOutput === "string" ? item.aggregatedOutput : "",
    command: typeof item.command === "string" ? item.command : undefined,
    cwd: typeof item.cwd === "string" ? item.cwd : undefined,
    status: typeof item.status === "string" ? item.status : undefined,
    exitCode: typeof item.exitCode === "number" ? item.exitCode : undefined,
    durationMs: typeof item.durationMs === "number" ? item.durationMs : undefined,
  };
  if (kind === "imageView") return { id: String(item.id), kind, text: "Looked at a screenshot", status: "completed" };
  if (kind === "contextCompaction") return { id: String(item.id), kind: "separator", text: "Earlier steps summarised to keep this chat fast" };
  if (kind === "mcpToolCall") {
    const title = toolTitle(String(item.tool ?? ""), item.arguments);
    const error = item.error && typeof item.error === "object" ? String((item.error as { message?: unknown }).message ?? "") : "";
    const detail = error || toolResultText(item.result);
    return { id: String(item.id), kind, text: detail ? `${title}\n\n${detail}` : title, status: typeof item.status === "string" ? item.status : undefined,
      durationMs: typeof item.durationMs === "number" ? item.durationMs : undefined };
  }
  const value = kind === "userMessage" && Array.isArray(content)
    ? content.map(part => part.type === "text" ? part.text ?? "" : `[${part.type ?? "attachment"}]`).join("\n")
    : item.text ?? item.aggregatedOutput ?? item.command ?? content ?? item;
  return { id: String(item.id), kind: kind === "agentMessage" ? "assistant" : kind === "userMessage" ? "user" : kind,
    text: typeof value === "string" ? value : JSON.stringify(value, null, 2), status: typeof item.status === "string" ? item.status : undefined };
}
export function mergeItem(items: ViewItem[], next: ViewItem, append = false, started = false): ViewItem[] {
  const index = items.findIndex(item => item.id === next.id);
  if (index < 0) return [...items, next];
  return items.map((item, position) => position !== index ? item : { ...item, ...next, status: next.status ?? item.status,
    command: next.command ?? item.command, cwd: next.cwd ?? item.cwd,
    exitCode: next.exitCode ?? item.exitCode, durationMs: next.durationMs ?? item.durationMs,
    text: append ? item.text + next.text : started || !next.text ? item.text : next.text });
}
export function approvalResponse(method: string, params: Record<string, unknown>, allow: boolean): Record<string, unknown> {
  if (["item/commandExecution/requestApproval", "item/fileChange/requestApproval"].includes(method)) return { decision: allow ? "accept" : "decline" };
  if (method === "item/permissions/requestApproval") {
    const requested = params.permissions;
    if (!requested || typeof requested !== "object" || Array.isArray(requested)) throw new Error("Invalid permission request");
    const permissions = requested as Record<string, unknown>;
    if (Object.keys(permissions).some(key => !["network", "fileSystem"].includes(key))) throw new Error("Unsupported permission profile");
    return { permissions: allow ? permissions : {}, scope: "turn" };
  }
  if (method === "mcpServer/elicitation/request") {
    if (!allow) return { action: "decline", content: null, _meta: null };
    if (isNativeMcpActionApproval(params)) return { action: "accept", content: {}, _meta: null };
  }
  throw new Error("This specialized request cannot be approved here. Deny it or stop the turn.");
}
export function isNativeMcpActionApproval(params: Record<string, unknown>): boolean {
  // This is the exact empty-form action-confirmation shape emitted by the
  // copied core. It is not permission to fill OAuth/device/other MCP forms.
  const schema = params.requestedSchema as Record<string, unknown> | undefined;
  const meta = params._meta as Record<string, unknown> | undefined;
  const properties = schema?.properties;
  return params.mode === "form" && typeof params.serverName === "string" && !!params.serverName.trim()
    && typeof params.message === "string" && !!params.message.trim()
    && !!schema && schema.type === "object" && !!properties && typeof properties === "object" && !Array.isArray(properties)
    && Object.keys(properties).length === 0 && (schema.required === undefined || (Array.isArray(schema.required) && schema.required.length === 0))
    && !!meta && meta.codex_approval_kind === "mcp_tool_call";
}
export function supportsApproval(method: string, params: Record<string, unknown> = {}): boolean {
  return ["item/commandExecution/requestApproval", "item/fileChange/requestApproval", "item/permissions/requestApproval"].includes(method)
    || (method === "mcpServer/elicitation/request" && isNativeMcpActionApproval(params));
}
export function activeTurn(turns: NativeTurn[]): NativeTurn | undefined {
  return [...turns].reverse().find(turn => turn.status === "inProgress");
}
export function resumeStatus(turns: NativeTurn[]): string {
  const active = activeTurn(turns);
  if (active) return "Conversation resumed — a native turn is still in progress";
  const latest = turns.at(-1);
  if (latest?.status === "interrupted") return "Interrupted conversation restored; unfinished streamed text may not be saved. No actions replayed.";
  if (latest?.status === "failed") return "Failed conversation restored; this is not a completed task. No actions replayed.";
  return "Conversation resumed; no tool actions replayed";
}
