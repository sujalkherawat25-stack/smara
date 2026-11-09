import { useState } from "react";
import { agentNameProblem, type AgentRecord } from "../agents";

export type AgentDraft = { id?: string; name: string; role: string; icon: string; instructions: string; folders: string[]; preset: boolean };
type Project = { workspace: string; label: string };
type Props = {
  roster: AgentRecord[]; counts: Record<string, number>; selected: string; projects: Project[]; disabled: boolean;
  onSelect: (id: string) => void; onSave: (draft: AgentDraft) => Promise<void>;
};

/** The agent list in the sidebar, with a form to create or edit one. Editing a built-in agent keeps it a built-in. */
export function AgentRoster({ roster, counts, selected, projects, disabled, onSelect, onSave }: Props) {
  const [editing, setEditing] = useState<AgentDraft | null>(null);
  const [problem, setProblem] = useState("");
  const startNew = () => { setProblem(""); setEditing({ name: "", role: "", icon: "🤖", instructions: "", folders: [], preset: false }); };
  const edit = (agent: AgentRecord) => {
    setProblem("");
    setEditing({ id: agent.id, name: agent.name, role: agent.role, icon: agent.icon, instructions: agent.instructions, folders: agent.folders, preset: agent.preset });
  };
  const update = (patch: Partial<AgentDraft>) => setEditing(current => current ? { ...current, ...patch } : current);
  return (
    <section className="agent-roster-section" aria-label="Agents">
      <div className="sidebar-section-heading"><span>Agents</span><button aria-label="New agent" disabled={disabled} onClick={startNew}>＋</button></div>
      <div className="agent-roster">
        {roster.map(agent => <div key={agent.id}>
          <button className="agent-row" aria-current={agent.id === selected ? "true" : undefined} disabled={disabled} onClick={() => onSelect(agent.id)}>
            <span aria-hidden="true">{agent.icon}</span>
            <span>{agent.name}<small>{agent.role}</small></span>
            <span className="agent-count">{counts[agent.id] ?? 0}</span>
          </button>
          <button className="text-button" aria-label={`Edit ${agent.name}`} disabled={disabled} onClick={() => edit(agent)}>Edit</button>
        </div>)}
      </div>
      {editing && <form className="agent-form" onSubmit={async event => {
        event.preventDefault();
        const message = agentNameProblem(editing.name);
        if (message) { setProblem(message); return; }
        setProblem("");
        await onSave({ ...editing, name: editing.name.trim() });
        setEditing(null);
      }}>
        <label>Name<input value={editing.name} maxLength={40} disabled={disabled} onChange={event => update({ name: event.target.value })} /></label>
        <label>Role<input value={editing.role} maxLength={60} disabled={disabled} onChange={event => update({ role: event.target.value })} /></label>
        <label>Instructions<textarea rows={3} value={editing.instructions} maxLength={1500} disabled={disabled} onChange={event => update({ instructions: event.target.value })} /></label>
        <fieldset>
          <legend>Folders it may use</legend>
          {projects.length ? projects.map(project => <label key={project.workspace}>
            <input type="checkbox" checked={editing.folders.includes(project.workspace)} disabled={disabled}
              onChange={event => update({ folders: event.target.checked ? [...editing.folders, project.workspace] : editing.folders.filter(item => item !== project.workspace) })} />
            {project.label}
          </label>) : <small>Add a project first.</small>}
        </fieldset>
        {problem && <p role="alert">{problem}</p>}
        <div><button type="submit" disabled={disabled}>Save agent</button><button type="button" onClick={() => setEditing(null)}>Cancel</button></div>
      </form>}
    </section>
  );
}
