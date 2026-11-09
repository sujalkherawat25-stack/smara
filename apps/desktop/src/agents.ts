/** Agent roster helpers. The executor stores agents and chat assignments (src/smara/native_agents.py). */
export type AgentRecord = {
  id: string; name: string; role: string; icon: string; instructions: string; preset: boolean;
  folders: string[]; origins: string[]; terminal: boolean; memory_scope: "project" | "personal"; model_profile: string;
};
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

/** Empty string when the name is usable; otherwise the reason to show the user. */
export function agentNameProblem(name: string): string {
  const trimmed = name.trim();
  if (!trimmed) return "Give the agent a name.";
  if (trimmed.length > 40) return "Keep the name under 40 characters.";
  return "";
}
