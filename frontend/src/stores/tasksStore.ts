/**
 * stores/tasksStore.ts — shared scheduled-tasks state.
 *
 * Used by the Settings → Tasks panel. Refreshes when the modal opens.
 * Per-task run history is fetched on-demand by the panel (not cached here)
 * since users rarely view multiple histories in a single session.
 */

import { create } from "zustand";
import {
  fetchTasks,
  setTaskEnabled as apiSetEnabled,
  deleteTask as apiDelete,
  type ScheduledTask,
} from "@/lib/tasks";

interface TasksState {
  items: ScheduledTask[];
  loaded: boolean;
  refresh: () => Promise<void>;
  toggle: (taskId: string, enabled: boolean) => Promise<boolean>;
  remove: (taskId: string) => Promise<boolean>;
}

export const useTasksStore = create<TasksState>((set, get) => ({
  items: [],
  loaded: false,
  refresh: async () => {
    const items = await fetchTasks();
    set({ items, loaded: true });
  },
  toggle: async (taskId, enabled) => {
    const updated = await apiSetEnabled(taskId, enabled);
    if (!updated) return false;
    set({
      items: get().items.map((t) => (t.id === taskId ? updated : t)),
    });
    return true;
  },
  remove: async (taskId) => {
    const ok = await apiDelete(taskId);
    if (ok) set({ items: get().items.filter((t) => t.id !== taskId) });
    return ok;
  },
}));
