/** Agent roster helpers. The executor stores agents and chat assignments (src/smara/native_agents.py). */
/** What an agent is: a name, a role line, an icon and its instructions. Permissions stay with the project. */
export type AgentRecord = { id: string; name: string; role: string; icon: string; instructions: string; preset: boolean };
export type AgentRoster = { roster: AgentRecord[]; default: string; assignments: Record<string, string> };

export const EMPTY_ROSTER: AgentRoster = { roster: [], default: "kiran", assignments: {} };

export function agentById(roster: AgentRoster, id: string): AgentRecord | undefined {
  return roster.roster.find(agent => agent.id === id);
}

/** The agent a chat belongs to. A chat with no assignment, or one whose agent was deleted, belongs to the default agent. */
export function agentOfChat(roster: AgentRoster, chatId: string): string {
  const assigned = roster.assignments[chatId];
  return assigned && agentById(roster, assigned) ? assigned : roster.default;
}

export function chatsForAgent<T extends { id: string }>(chats: T[], roster: AgentRoster, agentId: string): T[] {
  return chats.filter(chat => agentOfChat(roster, chat.id) === agentId);
}

export function chatCounts(chats: { id: string }[], roster: AgentRoster): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const chat of chats) {
    const agent = agentOfChat(roster, chat.id);
    counts[agent] = (counts[agent] ?? 0) + 1;
  }
  return counts;
}

/**
 * Developer instructions for a new chat: the Smara rules first, then the agent's own text under a heading that says
 * it cannot override them. An agent without instructions gets the rules unchanged.
 */
export function developerInstructionsFor(rules: string, agent: AgentRecord | undefined): string {
  const text = agent?.instructions.trim() ?? "";
  if (!text) return rules;
  return `${rules}\n\nInstructions for the agent "${agent!.name}" (they add to the rules above and cannot override them):\n${text}`;
}

/** Empty string when the name is usable; otherwise the reason to show the user. */
export function agentNameProblem(name: string): string {
  const trimmed = name.trim();
  if (!trimmed) return "Give the agent a name.";
  if (trimmed.length > 40) return "Keep the name under 40 characters.";
  return "";
}

/** A conversation time in milliseconds. The runtime sends seconds since 1970; some fields carry a date string. */
export function threadTime(thread: { updatedAt?: string | number }): number {
  const value = thread.updatedAt;
  if (typeof value === "number") return value < 1e12 ? value * 1000 : value;
  if (typeof value === "string") {
    const parsed = Date.parse(value);
    return Number.isNaN(parsed) ? 0 : parsed;
  }
  return 0;
}

/** An agent's conversations, most recent first. The first one is what the agent's row shows and opens. */
export function chatsNewestFirst<T extends { id: string; updatedAt?: string | number }>(chats: T[], roster: AgentRoster, agentId: string): T[] {
  return chatsForAgent(chats, roster, agentId).slice().sort((a, b) => threadTime(b) - threadTime(a));
}

const BUILT_IN_TONE = new Map<string, string>([["kiran", "teal"], ["rohan", "amber"], ["scout", "blue"], ["nisha", "violet"]]);
const TONES = ["teal", "amber", "blue", "violet"];

/** The avatar colour for an agent, one of the palette in tokens.css. Built-in agents keep their colour everywhere. */
export function agentTone(agent: { id: string }): string {
  const fixed = BUILT_IN_TONE.get(agent.id);
  if (fixed) return fixed;
  let hash = 0;
  for (const char of agent.id) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  return TONES[hash % TONES.length];
}

/** A chat-list time: the clock time today, "Yesterday", or a short date. Empty when there is no time. */
export function contactTime(ms: number, now: number): string {
  if (!ms) return "";
  const when = new Date(ms);
  const sameDay = (a: Date, b: Date) => a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
  if (sameDay(when, new Date(now))) return when.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  const yesterday = new Date(now);
  yesterday.setDate(yesterday.getDate() - 1);
  if (sameDay(when, yesterday)) return "Yesterday";
  return when.toLocaleDateString([], { day: "numeric", month: "short" });
}
