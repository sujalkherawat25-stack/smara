/**
 * Toast/notification store — used to surface async events from the backend
 * (reminder fires, scheduled-task results) that arrive outside the active
 * chat turn over the SSE event channel.
 */

import { create } from "zustand";

export type NotificationKind = "reminder" | "task_result" | "info" | "error";

export interface ToastNotification {
  id: string;
  kind: NotificationKind;
  title: string;
  body: string;
  // Action button (optional): label + onClick handler key resolved by the renderer.
  action?: { label: string; payload?: Record<string, unknown> };
  // When the toast should auto-dismiss (ms from now). Pass 0 to require manual dismiss.
  ttl_ms: number;
  createdAt: number;
}

interface NotificationsState {
  toasts: ToastNotification[];
  push: (n: Omit<ToastNotification, "id" | "createdAt">) => string;
  dismiss: (id: string) => void;
  clear: () => void;
}

export const useNotificationsStore = create<NotificationsState>((set) => ({
  toasts: [],

  push: (n) => {
    const id = crypto.randomUUID();
    const toast: ToastNotification = {
      ...n,
      id,
      createdAt: Date.now(),
    };
    set((s) => ({ toasts: [...s.toasts, toast] }));
    if (n.ttl_ms > 0) {
      window.setTimeout(() => {
        set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) }));
      }, n.ttl_ms);
    }
    return id;
  },

  dismiss: (id) =>
    set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),

  clear: () => set({ toasts: [] }),
}));


// ── Browser Notification API helper ─────────────────────────────────────────
// Asks for permission once and remembers the answer. Safe to call repeatedly
// (no-op when permission is already granted/denied).

let _permissionRequested = false;

export async function ensureNotificationPermission(): Promise<NotificationPermission> {
  if (typeof window === "undefined" || !("Notification" in window)) {
    return "denied";
  }
  if (Notification.permission === "granted" || Notification.permission === "denied") {
    return Notification.permission;
  }
  if (_permissionRequested) return Notification.permission;
  _permissionRequested = true;
  try {
    return await Notification.requestPermission();
  } catch {
    return "denied";
  }
}

export function showBrowserNotification(title: string, body: string): void {
  if (typeof window === "undefined" || !("Notification" in window)) return;
  if (Notification.permission !== "granted") return;
  try {
    new Notification(title, { body, icon: "/favicon.ico", tag: "memento" });
  } catch {
    // Some browsers throw if the document isn't focused — silent failure is fine.
  }
}
