/** Presentation only; native Rust owns execution and permissions. */
export type ViewItem = { id: string; kind: string; text: string; status?: string };
export type NativeTurn = { id: string; status: string; items: Record<string, unknown>[] };
export type WorkerView = { id: string; status: string; cwd?: string };
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
  return (thread.name?.trim() || thread.preview?.trim() || "New conversation").replace(/\s+/g, " ").slice(0, 96);
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
