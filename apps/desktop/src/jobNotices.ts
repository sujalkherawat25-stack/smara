/** "Kiran finished" notices: what changed in the team's background jobs since the last look. Pure, so it can be tested without the app. */
import type { Goal } from "./goalApi";

export type JobNotice = { id: string; goalId: string; tone: "done" | "attention"; text: string };
const WORKING = new Set(["queued", "running", "awaiting_approval"]);

const clip = (text: string, size: number) => { const line = text.replace(/\s+/g, " ").trim(); return line.length > size ? line.slice(0, size - 1) + "…" : line; };

/**
 * previous: job id -> state from the last snapshot, or null for the first snapshot (it only sets the baseline, so opening the app
 * does not announce old work). Returns the new state map and a notice for each job that just finished, needs you, or paused.
 */
export function jobNotices(previous: Record<string, string> | null, goals: Goal[], nameOf: (agentId: string | undefined) => string): { states: Record<string, string>; notices: JobNotice[] } {
  const states: Record<string, string> = {};
  const notices: JobNotice[] = [];
  for (const goal of goals) {
    states[goal.id] = goal.state;
    const before = previous?.[goal.id];
    if (!before || !WORKING.has(before) || before === goal.state) continue;
    const who = nameOf(goal.spec.agent_id);
    const summary = String(goal.checkpoint?.summary || "").trim();
    if (goal.state === "ready_for_review" || goal.state === "completed") {
      if (/^NOTHING_NEW/i.test(summary)) continue;       // a quiet watch is not news
      notices.push({ id: `${goal.id}:${goal.state}`, goalId: goal.id, tone: "done", text: `${who} finished: ${clip(summary || goal.spec.objective, 140)}` });
    } else if (goal.state === "blocked") {
      notices.push({ id: `${goal.id}:${goal.state}`, goalId: goal.id, tone: "attention", text: `${who} needs attention: ${clip(goal.detail || goal.spec.objective, 140)}` });
    } else if (goal.state === "paused") {
      notices.push({ id: `${goal.id}:${goal.state}`, goalId: goal.id, tone: "attention", text: `${who}'s job paused: ${clip(goal.detail || goal.spec.objective, 140)}` });
    }
  }
  return { states, notices };
}
