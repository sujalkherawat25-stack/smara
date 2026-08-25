/**
 * stores/googleStore.ts — shared Google (Gmail/Calendar) grant status.
 *
 * Refreshed when the Settings panel opens. Also refreshed automatically
 * after the redirect-back flow (App.tsx already shows a toast on
 * ?google=connected — we just need to know the new state).
 */

import { create } from "zustand";
import { fetchGoogleStatus, disconnectGoogle, type GoogleStatus } from "@/lib/google";

interface GoogleState {
  status: GoogleStatus | null;
  loaded: boolean;
  refresh: () => Promise<void>;
  disconnect: () => Promise<boolean>;
}

export const useGoogleStore = create<GoogleState>((set) => ({
  status: null,
  loaded: false,
  refresh: async () => {
    const s = await fetchGoogleStatus();
    set({ status: s, loaded: true });
  },
  disconnect: async () => {
    const ok = await disconnectGoogle();
    if (ok) {
      set({ status: { available: true, connected: false, features: [], granted_at: null } });
    }
    return ok;
  },
}));
