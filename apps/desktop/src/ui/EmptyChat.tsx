import type { AgentRecord } from "../agents";

/** Starter prompts per built-in agent. Each one fills the composer; the owner still reviews and sends it. */
const STARTERS: Record<string, string[]> = {
  kiran: ["Who owes me the most this month?", "Summarise the totals in my Tally export", "Check this sheet for duplicate invoices"],
  rohan: ["Draft a polite reminder for overdue dues", "Which customers are more than 30 days late?", "What is due this week?"],
  scout: ["Find the public price of a product on a shop's site", "List the contact details on a public business page"],
  nisha: ["Make a statement PDF for a customer", "Read the attached PDF and list the key figures", "Turn this sheet into a short summary document"],
};
const GENERAL = ["What can you help me with?", "Summarise my open dues", "Read the file I attach and tell me what it says"];

type Props = {
  agent: AgentRecord | undefined;
  projectLabel: string;
  hasModel: boolean;
  connecting: boolean;
  onPick: (text: string) => void;
};

/** The first thing in an empty chat: who you are talking to, and what to ask them. */
export function EmptyChat({ agent, projectLabel, hasModel, connecting, onPick }: Props) {
  const starters = hasModel && !connecting ? (agent && STARTERS[agent.id]) || GENERAL : [];
  return (
    <div className="empty-chat">
      <div className="empty-icon" aria-hidden="true">{agent?.icon ?? "✦"}</div>
      <h1>{agent ? `Ask ${agent.name}` : "Ask Smara"}</h1>
      <div className="empty-role">{agent?.role ? `${agent.role} · ${projectLabel}` : projectLabel}</div>
      {!hasModel && <p className="empty-hint">Add a model in Settings to get started.</p>}
      {hasModel && connecting && <p className="empty-hint">Preparing your project…</p>}
      {starters.length > 0 && <div className="starter-grid" role="list" aria-label="Ideas to start with">
        {starters.map(text => <button key={text} className="starter" role="listitem" onClick={() => onPick(text)}>{text}</button>)}
      </div>}
    </div>
  );
}
