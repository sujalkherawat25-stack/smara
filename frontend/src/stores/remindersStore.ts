/**
 * stores/remindersStore.ts — shared upcoming-reminders state.
 *
 * Auto-refreshes on app focus + when the settings modal opens. Lightweight:
 * just a list + the small mutation methods the UI needs.
 *
 * The agent's `set_reminder` tool inserts reminders server-side during a
 * chat. We don't currently push a refresh-event from chat → store, so a
 * brand-new reminder will appear when the user reopens the modal OR when
 * the tab regains focus. Good enough for v1.
 */

import { create } from "zustand";
import {
  fetchReminders,
  deleteReminder as apiDelete,
  type Reminder,
} from "@/lib/reminders";

interface RemindersState {
  items: Reminder[];
  loaded: boolean;
  refresh: () => Promise<void>;
  cancel: (reminderId: string) => Promise<boolean>;
}

export const useRemindersStore = create<RemindersState>((set, get) => ({
  items: [],
  loaded: false,
  refresh: async () => {
    const items = await fetchReminders(false);
    // Newest fire_at first → most-imminent at top.
    items.sort((a, b) => a.fire_at.localeCompare(b.fire_at));
    set({ items, loaded: true });
  },
  cancel: async (reminderId: string) => {
    const ok = await apiDelete(reminderId);
    if (ok) {
      // Optimistic local remove so the UI doesn't wait for a refetch.
      set({ items: get().items.filter((r) => r.id !== reminderId) });
    }
    return ok;
  },
}));
