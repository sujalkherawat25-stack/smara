import { create } from "zustand";
import type { ConversationMessage } from "@/types/memory";

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

function newConv(projectId?: string): Conversation {
  return {
    id: crypto.randomUUID(),
    conversationId: crypto.randomUUID(),
    title: "New conversation",
    messages: [],
    createdAt: new Date().toISOString(),
    lastUpdated: new Date().toISOString(),
    projectId,
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
      set((s) => {
        const next = s.conversations.filter((c) => c.id !== id);
        persist(next);
        const activeId =
          s.activeId === id ? (next[0]?.id ?? null) : s.activeId;
        return { conversations: next, activeId };
      });
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
  };
});
