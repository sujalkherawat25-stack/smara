/**
 * stores/telegramStore.ts — shared Telegram link state.
 *
 * One source of truth for "is this account linked to Telegram?" so the
 * header chip and the API Keys modal show the same answer without each
 * making its own /v1/auth/link/telegram/status call.
 *
 * Refresh policy: refreshed on app mount (post sign-in), every time the
 * API Keys modal opens, and after any link/unlink action. No polling at
 * idle — link status only changes via explicit user action.
 */

import { create } from "zustand";
import {
  fetchTelegramLinkStatus,
  unlinkTelegram as apiUnlink,
  type TelegramLinkStatus,
} from "@/lib/auth";

interface TelegramState {
  status: TelegramLinkStatus | null;
  loaded: boolean;
  refresh: () => Promise<void>;
  unlink: () => Promise<void>;
}

export const useTelegramStore = create<TelegramState>((set) => ({
  status: null,
  loaded: false,
  refresh: async () => {
    try {
      const s = await fetchTelegramLinkStatus();
      set({ status: s, loaded: true });
    } catch {
      // Treat hard failure as "unknown / not linked" so the UI stays usable.
      set({ status: { linked: false, linked_at: null, channel_user_preview: null }, loaded: true });
    }
  },
  unlink: async () => {
    await apiUnlink();
    set({ status: { linked: false, linked_at: null, channel_user_preview: null }, loaded: true });
  },
}));
