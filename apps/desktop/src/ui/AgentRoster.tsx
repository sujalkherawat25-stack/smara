/** What the owner types when creating or editing an agent. */
export type AgentDraft = { id?: string; name: string; role: string; icon: string; instructions: string; preset: boolean };

type Props = {
  draft: AgentDraft;
  problem: string;
  disabled: boolean;
  onChange: (patch: Partial<AgentDraft>) => void;
  onSubmit: () => void;
  onCancel: () => void;
};

/** The form to create or edit an agent. The sidebar shows it under the Agents heading while it is open. */
export function AgentForm({ draft, problem, disabled, onChange, onSubmit, onCancel }: Props) {
  return (
    <form className="agent-form" aria-label={draft.id ? "Edit agent" : "New agent"} onSubmit={event => { event.preventDefault(); onSubmit(); }}>
      <label>Name<input value={draft.name} maxLength={40} disabled={disabled} onChange={event => onChange({ name: event.target.value })} /></label>
      <label>Role<input value={draft.role} maxLength={60} placeholder="For example: Accounts" disabled={disabled} onChange={event => onChange({ role: event.target.value })} /></label>
      <label>Instructions<textarea rows={3} value={draft.instructions} maxLength={1500} disabled={disabled} onChange={event => onChange({ instructions: event.target.value })} /></label>
      <small>Instructions guide this agent in new chats. They cannot change Smara's rules or what the project allows.</small>
      {problem && <p role="alert">{problem}</p>}
      <div className="agent-form-actions">
        <button type="submit" disabled={disabled}>Save agent</button>
        <button type="button" onClick={onCancel}>Cancel</button>
      </div>
    </form>
  );
}
