/** Smara and the team: the manager first, then employees with what each one is doing right now. Pure helpers. */
import type { AgentRecord, AgentRoster } from "./agents";

export const MANAGER_ID = "smara";
/** Must match native_team.RULES_HEADING: standing rules are saved under this line in the instructions. */
export const RULES_HEADING = "Standing rules from the owner:";
/** What "Hire" puts in the manager's composer: hiring is explained to Smara in chat, in the owner's own words. */
export const HIRE_STARTER = "I want to hire someone to ";

type GoalLike = { id: string; state: string; spec: { agent_id?: string; objective: string; sandbox?: string }; lease?: string | null };

export function splitRoster(roster: AgentRoster): { manager: AgentRecord | undefined; employees: AgentRecord[] } {
  return { manager: roster.roster.find(agent => agent.id === MANAGER_ID), employees: roster.roster.filter(agent => agent.id !== MANAGER_ID) };
}

export function splitJob(instructions: string): { job: string; rules: string[] } {
  const at = instructions.indexOf(RULES_HEADING);
  if (at < 0) return { job: instructions.trim(), rules: [] };
  const rules = instructions.slice(at + RULES_HEADING.length).split(/\r?\n/).map(line => line.replace(/^\s*-\s*/, "").trim()).filter(Boolean);
  return { job: instructions.slice(0, at).trim(), rules };
}

export function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  return ((parts[0]?.[0] ?? "?") + (parts.length > 1 ? parts.at(-1)![0] : "")).toUpperCase();
}

const OPEN = new Set(["queued", "running", "awaiting_approval", "blocked", "paused"]);

export type MemberStatus = { tone: "needs" | "working" | "idle"; text: string };

/** What the sidebar says under a name: waiting for the owner first, then working, then the latest chat or the role. */
export function memberStatus(agentId: string, goals: GoalLike[], latestChat: string, role: string): MemberStatus {
  const mine = goals.filter(goal => (goal.spec.agent_id || MANAGER_ID) === agentId && OPEN.has(goal.state));
  const waiting = mine.find(goal => goal.state === "awaiting_approval" || goal.state === "blocked");
  if (waiting) return { tone: "needs", text: `Needs you · ${shortTask(waiting.spec.objective)}` };
  const working = mine.find(goal => goal.state === "running") ?? mine.find(goal => goal.state === "queued");
  if (working) return { tone: "working", text: `${working.state === "queued" ? "Up next" : "Working"} · ${shortTask(working.spec.objective)}` };
  return { tone: "idle", text: latestChat || "Ready for work" };
}

export function shortTask(text: string): string {
  const clean = text.replace(/\s+/g, " ").trim();
  return clean.length > 48 ? clean.slice(0, 47) + "…" : clean;
}

/** Only a task that edits project files keeps the chat from sending; employees reading and drafting do not. */
export function blocksChat(goals: GoalLike[]): boolean {
  return goals.some(goal => !!goal.lease && goal.spec.sandbox === "workspace-write");
}

export type Routine = { id: string; name: string; enabled: number; runs: number; max_runs: number; due: number; interval?: number; spec?: { agent_id?: string }; goal_state?: string };

/** Routines that belong to one employee (a routine with no employee belongs to the manager). */
export function routinesFor(all: Routine[], agentId: string): Routine[] {
  return all.filter(item => (item.spec?.agent_id || MANAGER_ID) === agentId);
}

/** One line for the job panel: what, how often, and where it stands. */
export function routineLine(item: Routine): string {
  const hours = (item.interval ?? 0) / 3600;
  const every = hours >= 168 ? "every week" : hours >= 24 ? "every day" : hours >= 1 ? "every hour" : "";
  const state = !item.enabled ? "paused" : item.goal_state && item.goal_state !== "completed" ? "waiting for you to accept the last report" : `next ${new Date(item.due * 1000).toLocaleString(undefined, { weekday: "short", hour: "numeric", minute: "2-digit" })}`;
  return `${item.name.replace(/^[^:]+:\s*/, "")}${every ? ` · ${every}` : ""} · ${state} · ${item.runs}/${item.max_runs} runs`;
}

/** Ids that are in the new roster but were not in the old one: employees who just joined. */
export function newMembers(before: string[], after: string[]): string[] {
  const known = new Set(before);
  return after.filter(id => !known.has(id));
}

/** True when two rosters show the same people with the same names, roles and jobs (so the sidebar need not redraw). */
export function sameRoster(a: AgentRoster, b: AgentRoster): boolean {
  const key = (r: AgentRoster) => JSON.stringify([r.default, r.roster.map(x => [x.id, x.name, x.role, x.icon, x.instructions]), r.assignments]);
  return key(a) === key(b);
}

const TEAM_CHANGING = new Set(["team_hire", "team_update", "team_remember", "team_remove"]);
/** A finished tool call that may have changed the team, so the sidebar re-reads it. */
export function changesTeam(item: Record<string, unknown>): boolean {
  return item.type === "mcpToolCall" && TEAM_CHANGING.has(String(item.tool ?? ""));
}
