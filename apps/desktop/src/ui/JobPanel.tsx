import type { AgentRecord } from "../agents";
import { MANAGER_ID, splitJob } from "../team";

/** An employee's job and the standing rules the owner gave them. Changing them is done by just saying so in chat. */
export default function JobPanel({ agent, onEdit, onClose }: { agent: AgentRecord; onEdit: () => void; onClose: () => void }) {
  const { job, rules } = splitJob(agent.instructions);
  const manager = agent.id === MANAGER_ID;
  return <section className="job-panel" aria-label={`${agent.name}'s job`}>
    <header><div><strong>{agent.name}</strong><small>{manager ? "Manager" : agent.role}</small></div><button className="text-button" onClick={onClose} aria-label="Close">Close</button></header>
    <h4>{manager ? "What Smara does" : "Job"}</h4>
    <p>{job || "No job written yet. Tell them in chat what they should do."}</p>
    <h4>Standing rules</h4>
    {rules.length ? <ul>{rules.map(rule => <li key={rule}>{rule}</li>)}</ul> : <p className="job-empty">None yet. Say “from now on …” in this chat and {agent.name} will ask to remember it.</p>}
    <p className="job-hint">{manager
      ? "Ask Smara to hire someone, rename itself, or hand a job to an employee. Team changes always ask you first."
      : `To change ${agent.name}'s job, tell ${agent.name} or Smara in chat. You confirm every change.`}</p>
    <button onClick={onEdit}>Edit by hand</button>
  </section>;
}
