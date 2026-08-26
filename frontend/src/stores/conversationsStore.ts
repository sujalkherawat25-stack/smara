import { create } from "zustand";
import type { ConversationMessage } from "@/types/memory";
import { smaraFetch, smaraModeEnabled } from "@/lib/smaraGateway";

export interface Conversation {
  id: string;           // unique UI id
  conversationId: string; // backend conversation_id
  title: string;
  messages: ConversationMessage[];
  createdAt: string;
  lastUpdated: string;
  // F8: set when this conversation was started while a Project was active
  // (projectsStore.currentProjectId). Lets a Project's workspace show its
  // own "Recent chats" list without any new backend endpoint — mirrors how
  // every other conversation list in this app is already client-side.
  projectId?: string;
  /** Hosted Smara workspace used when loading turns from the server. */
  workspaceId?: string;
}

interface HostedConversation {
  id: string;
  workspace_id: string;
  created_at: string;
  updated_at: string;
  first_message?: string | null;
}

interface HostedTurn {
  role: "user" | "assistant";
  content: string;
  model?: string | null;
  sequence: number;
  created_at: string;
}

interface ConversationsStore {
  conversations: Conversation[];
  activeId: string | null;

  // Create a brand-new conversation and make it active
  createNew: (projectId?: string) => Conversation;

  // Save / overwrite messages for a conversation
  save: (id: string, messages: ConversationMessage[], conversationId: string) => void;

  // Select a conversation (returns it)
  select: (id: string) => Conversation | undefined;

  // Delete
  remove: (id: string) => void;

  // Rename
  rename: (id: string, title: string) => void;
  /** Switch the local cache to the authenticated account namespace. */
  setAccountScope: (accountId: string | null) => void;
  /** Reconcile the sidebar with the hosted Smara conversation index. */
  hydrateFromServer: () => Promise<void>;
  /** Load one hosted conversation's turns without blocking the sidebar. */
  loadRemoteConversation: (sessionId: string, conversationId: string, workspaceId: string) => Promise<void>;
}

let accountScope: string | null = null;
const storageKey = () => accountScope ? `memoryos_conversations:${accountScope}` : "memoryos_conversations:anonymous";

function load(): Conversation[] {
  try {
    const raw = localStorage.getItem(storageKey());
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

function persist(convs: Conversation[]) {
  try {
    localStorage.setItem(storageKey(), JSON.stringify(convs));
  } catch {}
}

function makeTitle(messages: ConversationMessage[]): string {
  const first = messages.find((m) => m.role === "user");
  if (!first) return "New conversation";
  const t = first.content.trim().replace(/\n/g, " ");
  return t.length > 46 ? t.slice(0, 44) + "…" : t;
}

function titleFromPreview(preview: string | null | undefined): string {
  const text = String(preview || "").trim().replace(/\s+/g, " ");
  if (!text) return "Conversation";
  return text.length > 46 ? `${text.slice(0, 44)}…` : text;
}

function newConv(projectId?: string): Conversation {
  return {
    id: crypto.randomUUID(),
    conversationId: crypto.randomUUID(),
    title: "New conversation",
    messages: [],
    createdAt: new Date().toISOString(),
    lastUpdated: new Date().toISOString(),
    projectId,
    workspaceId: projectId || "default",
  };
}

export const useConversationsStore = create<ConversationsStore>((set, get) => {
  const initial = load();

  return {
    conversations: initial,
    activeId: initial[0]?.id ?? null,

    createNew: (projectId) => {
      const conv = newConv(projectId);
      set((s) => {
        const next = [conv, ...s.conversations];
        persist(next);
        return { conversations: next, activeId: conv.id };
      });
      return conv;
    },

    save: (id, messages, conversationId) => {
      set((s) => {
        const next = s.conversations.map((c) =>
          c.id === id
            ? {
                ...c,
                messages,
                conversationId,
                title: messages.length > 0 ? makeTitle(messages) : c.title,
                lastUpdated: new Date().toISOString(),
              }
            : c
        );
        // If id not found (first save), add it
        if (!next.find((c) => c.id === id)) {
          next.unshift({
            id,
            conversationId,
            title: makeTitle(messages),
            messages,
            createdAt: new Date().toISOString(),
            lastUpdated: new Date().toISOString(),
          });
        }
        persist(next);
        return { conversations: next };
      });
    },

    select: (id) => {
      set({ activeId: id });
      return get().conversations.find((c) => c.id === id);
    },

    remove: (id) => {
      const removed = get().conversations.find((item) => item.id === id);
      set((s) => {
        const next = s.conversations.filter((c) => c.id !== id);
        persist(next);
        const activeId =
          s.activeId === id ? (next[0]?.id ?? null) : s.activeId;
        return { conversations: next, activeId };
      });
      if (smaraModeEnabled() && removed?.conversationId) {
        void smaraFetch(`/v1/conversations/${encodeURIComponent(removed.conversationId)}`, { method: "DELETE" })
          .then((response) => {
            if (!response.ok && response.status !== 404) console.warn("[smara] conversation delete failed", response.status);
          })
          .catch(() => console.warn("[smara] conversation delete could not reach the hosted service"));
      }
    },

    rename: (id, title) => {
      set((s) => {
        const next = s.conversations.map((c) => (c.id === id ? { ...c, title } : c));
        persist(next);
        return { conversations: next };
      });
    },

    setAccountScope: (accountId) => {
      if (accountScope === accountId) return;
      accountScope = accountId;
      const scoped = load();
      set({ conversations: scoped, activeId: scoped[0]?.id ?? null });
    },

    hydrateFromServer: async () => {
      if (!smaraModeEnabled() || !accountScope) return;
      const response = await smaraFetch("/v1/conversations");
      if (!response.ok) throw new Error(`Could not load hosted conversations (${response.status}).`);
      const payload = await response.json() as { conversations?: HostedConversation[] };
      const remote = Array.isArray(payload.conversations) ? payload.conversations : [];
      set((state) => {
        const byConversationId = new Map(state.conversations.map((item) => [item.conversationId, item]));
        const remoteIds = new Set(remote.map((item) => item.id));
        const hosted = remote.map((item) => {
          const local = byConversationId.get(item.id);
          return {
            id: local?.id ?? crypto.randomUUID(),
            conversationId: item.id,
            title: local?.title && local.title !== "New conversation"
              ? local.title
              : titleFromPreview(item.first_message),
            messages: local?.messages ?? [],
            createdAt: local?.createdAt ?? item.created_at,
            lastUpdated: item.updated_at,
            projectId: local?.projectId,
            workspaceId: item.workspace_id || "default",
          } satisfies Conversation;
        });
        // Keep an empty locally-created draft until its first turn is sent;
        // discard stale message caches when the server no longer owns them.
        const drafts = state.conversations.filter((item) => !remoteIds.has(item.conversationId) && item.messages.length === 0);
        const next = [...hosted, ...drafts];
        persist(next);
        return { conversations: next, activeId: state.activeId && next.some((item) => item.id === state.activeId) ? state.activeId : next[0]?.id ?? null };
      });
    },

    loadRemoteConversation: async (sessionId, conversationId, workspaceId) => {
      if (!smaraModeEnabled() || !accountScope) return;
      const query = new URLSearchParams({ workspace_id: workspaceId || "default" });
      const response = await smaraFetch(`/v1/conversations/${encodeURIComponent(conversationId)}/turns?${query.toString()}`);
      if (!response.ok) throw new Error(`Could not load conversation (${response.status}).`);
      const payload = await response.json() as { turns?: HostedTurn[] };
      const turns = Array.isArray(payload.turns) ? payload.turns : [];
      const messages: ConversationMessage[] = turns
        .filter((turn) => (turn.role === "user" || turn.role === "assistant") && typeof turn.content === "string")
        .map((turn) => ({
          id: `${conversationId}:${turn.sequence}`,
          role: turn.role,
          content: turn.content,
          timestamp: turn.created_at,
        }));
      // Do not let a slow response for an old click replace the currently
      // selected conversation.
      set((state) => {
        if (state.activeId !== sessionId) return state;
        const next = state.conversations.map((item) => item.id === sessionId ? { ...item, messages } : item);
        persist(next);
        return { conversations: next };
      });
    },
  };
});
