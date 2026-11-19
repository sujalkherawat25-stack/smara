import { useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import type { AgentRecord } from "../agents";
type SkillDetail = { name: string; when: string; steps: number; version: number; uses: number; steps_text: string[]; checks: string[]; output: string; never: string[] };
import { MANAGER_ID, splitJob, routinesFor, routineLine, type Routine } from "../team";

/** An employee's job and the standing rules the owner gave them. Changing them is done by just saying so in chat. */
export default function JobPanel({ agent, onEdit, onClose }: { agent: AgentRecord; onEdit: () => void; onClose: () => void }) {
  const { job, rules } = splitJob(agent.instructions);
  const manager = agent.id === MANAGER_ID;
  const [routines, setRoutines] = useState<Routine[]>([]);
  const [skills, setSkills] = useState<SkillDetail[]>([]);
  const [openSkill, setOpenSkill] = useState("");
  const loadSkills = async (action = "skills", name = "") => {
    try {
      const value = await invoke<{ skills?: SkillDetail[] }>("native_manage", { request: { operation: "agents", action, agent_id: agent.id, name } });
      setSkills(value.skills ?? []);
    } catch { setSkills([]); }
  };
  useEffect(() => { void loadSkills(); }, [agent.id, agent.skills?.length]);
  const load = async (action = "status", values: Record<string, unknown> = {}) => {
    try {
      const value = await invoke<{ schedules?: Routine[] }>("native_manage", { request: { operation: "background", action, ...values } });
      setRoutines(routinesFor(value.schedules ?? [], agent.id));
    } catch { setRoutines([]); }
  };
  useEffect(() => { void load(); }, [agent.id]);
  return <section className="job-panel" aria-label={`${agent.name}'s job`}>
    <header><div><strong>{agent.name}</strong><small>{manager ? "Manager" : agent.role}</small></div><button className="text-button" onClick={onClose} aria-label="Close">Close</button></header>
    <h4>{manager ? "What Smara does" : "Job"}</h4>
    <p>{job || "No job written yet. Tell them in chat what they should do."}</p>
    <h4>Standing rules</h4>
    {rules.length ? <ul>{rules.map(rule => <li key={rule}>{rule}</li>)}</ul> : <p className="job-empty">None yet. Say “from now on …” in this chat and {agent.name} will ask to remember it.</p>}
    <h4>Skills</h4>
    {skills.length ? <ul className="job-skills">{skills.map(skill => <li key={skill.name}>
      <button className="skill-head" aria-expanded={openSkill === skill.name} onClick={() => setOpenSkill(current => current === skill.name ? "" : skill.name)}>
        <strong>{skill.name}</strong><small>{skill.when}</small>
        <span className="skill-meta">{skill.steps} steps · v{skill.version} · used {skill.uses}×</span>
      </button>
      {openSkill === skill.name && <div className="skill-body">
        <ol>{skill.steps_text.map((step, i) => <li key={i}>{step}</li>)}</ol>
        {!!skill.checks.length && <p><b>Checks:</b> {skill.checks.join("; ")}</p>}
        {skill.output && <p><b>Output:</b> {skill.output}</p>}
        {!!skill.never.length && <p><b>Never:</b> {skill.never.join("; ")}</p>}
        <button className="text-button" onClick={() => { if (window.confirm(`Forget “${skill.name}”? ${agent.name} will stop doing it this way.`)) void loadSkills("forget_skill", skill.name); }}>Forget</button>
      </div>}
    </li>)}</ul>
      : <p className="job-empty">None yet. Show {agent.name} how a job is done once (“this is how I want …”) and they will ask to save it as a skill.</p>}
    <h4>Routines</h4>
    {routines.length ? <ul className="job-routines">{routines.map(item => <li key={item.id}><span>{routineLine(item)}</span>
      <button className="text-button" onClick={() => void load(item.enabled ? "pause" : "enable", { id: item.id })}>{item.enabled ? "Pause" : "Resume"}</button></li>)}</ul>
      : <p className="job-empty">None. Say “every morning at 9, check …” and {agent.name} will ask to set it up.</p>}
    <p className="job-hint">{manager
      ? "Ask Smara to hire someone, rename itself, or hand a job to an employee. Team changes always ask you first."
      : `To change ${agent.name}'s job, tell ${agent.name} or Smara in chat. You confirm every change.`}</p>
    <button onClick={onEdit}>Edit by hand</button>
  </section>;
}
