/** Pure helpers for the task board: several agents working at once, each a background task (a "goal"). */
import type { Goal, GoalState, InputHolder } from "./goalApi";

export type Column = "needs_you" | "working" | "queued" | "stopped" | "done";
export const COLUMNS: { id: Column; label: string }[] = [
  { id: "needs_you", label: "Needs you" },
  { id: "working", label: "Working" },
  { id: "queued", label: "Waiting to start" },
  { id: "stopped", label: "Paused or stuck" },
  { id: "done", label: "Done" },
];

const COLUMN_OF: Record<GoalState, Column> = {
  awaiting_approval: "needs_you", ready_for_review: "needs_you", running: "working", queued: "queued",
  paused: "stopped", blocked: "stopped", completed: "done", stopped: "done",
};

export function columnOf(goal: Pick<Goal, "state" | "approvals">): Column {
  if (goal.approvals?.length) return "needs_you";
  return COLUMN_OF[goal.state] ?? "queued";
}

export function byColumn(goals: Goal[]): Record<Column, Goal[]> {
  const out = { needs_you: [], working: [], queued: [], stopped: [], done: [] } as Record<Column, Goal[]>;
  for (const goal of goals) out[columnOf(goal)].push(goal);
  for (const list of Object.values(out)) list.sort((a, b) => b.updated - a.updated);
  return out;
}

/** What an approval asks, in plain words: the native message if there is one, else the kind of request. */
export function approvalText(request: { method: string; params: Record<string, unknown> }): string {
  const message = typeof request.params.message === "string" ? request.params.message.trim() : "";
  if (message) return message.slice(0, 400);
  if (request.method === "item/commandExecution/requestApproval") return `Run a command: ${String(request.params.command ?? "").slice(0, 200)}`;
  if (request.method === "item/fileChange/requestApproval") return "Change files in the project";
  if (request.method === "item/permissions/requestApproval") return "Use more access (network or files) for this step";
  return request.method;
}

export function projectName(workspace: string, projects: { workspace: string; label: string }[]): string {
  const match = projects.find(p => p.workspace.toLowerCase() === workspace.toLowerCase());
  return match?.label || workspace.split(/[\\/]/).filter(Boolean).pop() || workspace;
}

/** Who has the mouse and keyboard, in words. */
export function inputLine(holder: InputHolder | null | undefined): string {
  if (!holder) return "Nobody is using the mouse and keyboard.";
  return `${holder.label || "A Smara agent"} is using the mouse and keyboard.`;
}

export function canPause(goal: Goal): boolean { return ["queued", "running", "awaiting_approval"].includes(goal.state); }
export function canResume(goal: Goal): boolean { return !goal.lease && ["paused", "blocked"].includes(goal.state); }
export function canStop(goal: Goal): boolean { return !["completed", "stopped"].includes(goal.state); }
export function canDismiss(goal: Goal): boolean { return !goal.lease && ["completed", "stopped"].includes(goal.state); }

export function title(goal: Goal): string {
  const text = goal.spec.objective.replace(/\s+/g, " ").trim();
  return text.length > 90 ? text.slice(0, 89) + "…" : text;
}
