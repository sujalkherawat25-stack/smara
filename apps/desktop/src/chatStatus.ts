import type { ViewItem } from "./nativeView";
export function waitingReason(items: ViewItem[], approvalCount: number, now: number, lastEvent: number): string {
  if (approvalCount) return "Waiting for your response · pending actions are not approved";
  const tool = [...items].reverse().find(item => item.status === "inProgress" && !["assistant", "user"].includes(item.kind));
  let detail = "Waiting for the model response";
  if (tool?.kind === "commandExecution") detail = "Running a command";
  else if (tool?.kind === "collabAgentToolCall") detail = "Waiting for worker results";
  else if (tool) detail = "Waiting for a tool result";
  const quiet = Math.max(0, Math.floor((now - lastEvent) / 1000));
  return quiet >= 30 ? `${detail} · no new events for ${quiet}s. Stop remains available; a freeze is not confirmed.` : `${detail} · Stop is available below`;
}
export type InputQuestion = { id: string; question: string; isSecret?: boolean; options?: { label: string; description: string }[] | null };
export function requestQuestions(params: Record<string, unknown>): InputQuestion[] | undefined {
  const value = params.questions;
  if (!Array.isArray(value) || !value.length || value.length > 20) return;
  const ids = new Set<string>();
  for (const item of value) {
    if (!item || typeof item !== "object" || typeof item.id !== "string" || !item.id || item.id.length > 256 || ids.has(item.id)
        || typeof item.question !== "string" || !item.question || item.question.length > 20000
        || item.isSecret !== undefined && typeof item.isSecret !== "boolean") return;
    if (item.options != null && (!Array.isArray(item.options) || item.options.length > 50 || item.options.some((option: unknown) =>
      !option || typeof option !== "object" || typeof (option as {label?: unknown}).label !== "string"
      || !(option as {label: string}).label || (option as {label: string}).label.length > 1000
      || typeof (option as {description?: unknown}).description !== "string"
      || (option as {description: string}).description.length > 20000))) return;
    ids.add(item.id);
  }
  return value as InputQuestion[];
}
