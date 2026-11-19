import { useEffect, useState } from "react";

export type ArchivedChat = { id: string; name?: string | null; preview?: string; updatedAt?: string | number };
export type ArchivedActions = {
  list: () => Promise<ArchivedChat[]>;
  restore: (chat: ArchivedChat) => Promise<void>;
  remove: (chat: ArchivedChat) => Promise<void>;
  agentOf: (chatId: string) => string;
};

const label = (chat: ArchivedChat) => (chat.name || chat.preview || "Untitled chat").replace(/\s+/g, " ").slice(0, 90);
const when = (chat: ArchivedChat) => {
  const value = typeof chat.updatedAt === "number" ? (chat.updatedAt < 1e12 ? chat.updatedAt * 1000 : chat.updatedAt) : Date.parse(String(chat.updatedAt ?? ""));
  return Number.isFinite(value) && value > 0 ? new Date(value).toLocaleDateString([], { day: "numeric", month: "short", year: "numeric" }) : "";
};

/** Settings > Archived chats: archived conversations of this project, with Restore and Delete. */
export default function NativeArchived({ actions, locked }: { actions?: ArchivedActions; locked: boolean }) {
  const [chats, setChats] = useState<ArchivedChat[] | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const load = async () => {
    if (!actions) return;
    try { setChats(await actions.list()); setError(""); } catch (reason) { setError(String(reason)); setChats([]); }
  };
  useEffect(() => { void load(); }, [actions]);
  const run = async (chat: ArchivedChat, work: () => Promise<void>) => {
    setBusy(chat.id); setError("");
    try { await work(); await load(); } catch (reason) { setError(String(reason)); } finally { setBusy(""); }
  };
  return <div className="set-page archived-page">
    <h2>Archived chats</h2>
    <p>Chats you archived are kept here, out of your agents' lists. Restore one to see it in its agent's chat again.</p>
    {!actions && <p className="native-warning">Open a project first.</p>}
    {error && <p className="native-error" role="alert">{error}</p>}
    {chats === null && actions && <p>Loading…</p>}
    {chats?.length === 0 && <p className="inbox-empty">No archived chats.</p>}
    {!!chats?.length && <ul className="archived-list">{chats.map(chat => <li key={chat.id}>
      <div><strong>{label(chat)}</strong><small>{[actions?.agentOf(chat.id), when(chat)].filter(Boolean).join(" · ")}</small></div>
      <button disabled={locked || !!busy} onClick={() => void run(chat, () => actions!.restore(chat))}>Restore</button>
      <button className="danger-action" disabled={locked || !!busy} onClick={() => { if (window.confirm(`Delete “${label(chat)}” for good? This cannot be undone.`)) void run(chat, () => actions!.remove(chat)); }}>Delete</button>
    </li>)}</ul>}
  </div>;
}
