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

/** The form to create or edit an agent. It opens as a dialog over the chat; Escape or Cancel closes it. */
export function AgentForm({ draft, problem, disabled, onChange, onSubmit, onCancel }: Props) {
  const title = draft.id ? "Edit agent" : "New agent";
  return (
    <form className="agent-form" aria-label={title} onKeyDown={event => { if (event.key === "Escape") { event.preventDefault(); onCancel(); } }} onSubmit={event => { event.preventDefault(); onSubmit(); }}>
      <h2>{title}</h2>
      <label>Name<input autoFocus value={draft.name} maxLength={40} disabled={disabled} onChange={event => onChange({ name: event.target.value })} /></label>
      <label>Role<input value={draft.role} maxLength={60} placeholder="For example: Accounts" disabled={disabled} onChange={event => onChange({ role: event.target.value })} /></label>
      <label>Instructions<textarea rows={3} value={draft.instructions} maxLength={1500} disabled={disabled} onChange={event => onChange({ instructions: event.target.value })} /></label>
      <small>Instructions guide this agent in new chats. They cannot change Smara's rules or what the project allows.</small>
      {problem && <p role="alert">{problem}</p>}
      <div className="agent-form-actions">
        <button type="submit" className="primary" disabled={disabled}>Save agent</button>
        <button type="button" onClick={onCancel}>Cancel</button>
      </div>
    </form>
  );
}
