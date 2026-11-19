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
