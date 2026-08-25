import { create } from "zustand";
import type { Memory, PendingMemory } from "@/types/memory";

interface MemoryState {
  approved: Memory[];
  pending: PendingMemory[];
  /** Bumped every time a memory-mutating tool fires (save/update/forget).
   *  MemoryPanel watches this and refetches when it changes — keeps the
   *  UI in sync with what the agent just wrote/deleted, since the panel
   *  only loads on mount and otherwise misses background updates. */
  dirtyTick: number;
  setApproved: (memories: Memory[]) => void;
  applyOperation: (memory: Memory, operation: string) => void;
  setPending: (memories: PendingMemory[]) => void;
  updatePendingText: (id: string, text: string) => void;
  removePending: (ids: string[]) => void;
  removeApproved: (id: string) => void;
  /** Called by chatStore when an SSE tool_result for save/update/forget
   *  memory lands. Triggers the MemoryPanel's refetch effect. */
  markDirty: () => void;
}

export const useMemoryStore = create<MemoryState>((set) => ({
  approved: [],
  pending: [],
  dirtyTick: 0,

  setApproved: (memories) => set({ approved: memories }),

  markDirty: () => set((s) => ({ dirtyTick: s.dirtyTick + 1 })),

  applyOperation: (memory, operation) =>
    set((s) => {
      if (operation === "ADD") return { approved: [memory, ...s.approved] };
      if (operation === "UPDATE")
        return { approved: s.approved.map((m) => (m.id === memory.id ? memory : m)) };
      if (operation === "DELETE")
        return { approved: s.approved.filter((m) => m.id !== memory.id) };
      return {};
    }),

  setPending: (memories) => set({ pending: memories }),

  updatePendingText: (id, text) =>
    set((s) => ({
      pending: s.pending.map((m) => (m.id === id ? { ...m, text } : m)),
    })),

  removePending: (ids) =>
    set((s) => ({
      pending: s.pending.filter((m) => !ids.includes(m.id)),
    })),

  removeApproved: (id) =>
    set((s) => ({ approved: s.approved.filter((m) => m.id !== id) })),
}));
