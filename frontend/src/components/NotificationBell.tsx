/**
 * NotificationBell — header bell that drains the proactive-fallback queue.
 *
 * Why it exists:
 *   Reminders + scheduled-task results normally land in Telegram. If the user
 *   hasn't linked Telegram (or Telegram is down), the backend pushes them onto
 *   a per-account Redis list at `notify:web:{account_id}`. This component is
 *   the user's way of seeing those notifications:
 *
 *     • Polls GET /v1/memento/notifications every 30 s.
 *     • Shows a badge with the unread count.
 *     • Click → dropdown listing the messages, newest first.
 *     • Per-row "✕" calls POST /notifications/ack/{id} to clear that entry.
 *     • "Clear all" calls POST /notifications/ack-all.
 *
 * The store is purely fetched-on-demand — no SSE plumbing. The 30-second
 * poll cadence is the upper bound on how stale the bell can be; that's
 * acceptable because Telegram is the primary channel for users who care
 * about real-time delivery (the web bell exists for the fallback case).
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { Bell, X } from "lucide-react";
import { smaraModeEnabled } from "@/lib/smaraGateway";

interface WebNotification {
  id: string;
  message: string;
  created_at: string; // ISO timestamp
}

const POLL_INTERVAL_MS = 30_000;
const API_BASE = (import.meta.env.VITE_API_URL as string | undefined) || "";

async function fetchNotifications(): Promise<WebNotification[]> {
  // Native Smara task updates are rendered in Work/Activity and local toast
  // state. Never probe the retired Memento notification queue.
  if (smaraModeEnabled()) return [];
  const r = await fetch(`${API_BASE}/v1/notifications`, { credentials: "include" });
  if (!r.ok) return [];
  const data = (await r.json()) as { notifications?: WebNotification[] };
  return data.notifications ?? [];
}

async function ackNotification(id: string): Promise<void> {
  await fetch(`${API_BASE}/v1/notifications/ack/${id}`, {
    method: "POST",
    credentials: "include",
  });
}

async function ackAll(): Promise<void> {
  await fetch(`${API_BASE}/v1/notifications/ack-all`, {
    method: "POST",
    credentials: "include",
  });
}

function timeAgo(iso: string): string {
  const t = new Date(iso).getTime();
  if (!t) return "";
  const delta = Math.max(0, Date.now() - t);
  const m = Math.floor(delta / 60_000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  return `${d}d ago`;
}

export default function NotificationBell() {
  const [items, setItems] = useState<WebNotification[]>([]);
  const [open, setOpen] = useState(false);
  const pollRef = useRef<number | null>(null);

  const refresh = useCallback(async () => {
    try {
      const next = await fetchNotifications();
      setItems(next);
    } catch {/* network blip — silent */}
  }, []);

  // Initial fetch + 30s polling. Cleanup on unmount.
  useEffect(() => {
    void refresh();
    pollRef.current = window.setInterval(() => void refresh(), POLL_INTERVAL_MS);
    return () => {
      if (pollRef.current !== null) window.clearInterval(pollRef.current);
    };
  }, [refresh]);

  // Close the dropdown on outside click.
  const wrapperRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (wrapperRef.current && !wrapperRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  const count = items.length;
  const hasUnread = count > 0;

  async function handleAck(id: string) {
    // Optimistic remove; revert on failure isn't critical (next poll fixes it).
    setItems((prev) => prev.filter((n) => n.id !== id));
    try { await ackNotification(id); } catch { void refresh(); }
  }

  async function handleAckAll() {
    setItems([]);
    try { await ackAll(); } catch { void refresh(); }
  }

  return (
    <div ref={wrapperRef} className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        title={hasUnread ? `${count} notification${count > 1 ? "s" : ""}` : "Notifications"}
        className="relative grid place-items-center w-8 h-8 rounded-lg transition-all"
        style={{
          background: "var(--bg-elevated)",
          border: "1px solid var(--border-default)",
          color: hasUnread ? "var(--accent)" : "var(--text-secondary)",
        }}
      >
        <Bell size={14} />
        {hasUnread && (
          <span
            className="absolute -top-1 -right-1 min-w-[16px] h-[16px] px-1 rounded-full text-[10px] font-bold grid place-items-center"
            style={{
              background: "#ef4444",
              color: "#fff",
              border: "1.5px solid var(--bg-base)",
            }}
          >
            {count > 99 ? "99+" : count}
          </span>
        )}
      </button>

      {open && (
        <div
          className="absolute right-0 mt-2 w-[320px] max-h-[420px] rounded-xl overflow-hidden flex flex-col z-50"
          style={{
            background: "var(--bg-surface)",
            border: "1px solid var(--border-default)",
            boxShadow: "var(--shadow-lg)",
          }}
        >
          {/* Header */}
          <div
            className="flex items-center justify-between px-4 py-2.5 shrink-0"
            style={{ borderBottom: "1px solid var(--border-dim)" }}
          >
            <p className="text-[12px] font-semibold uppercase tracking-wider" style={{ color: "var(--text-secondary)" }}>
              Notifications
            </p>
            {hasUnread && (
              <button
                onClick={handleAckAll}
                className="text-[11px] transition-all active:opacity-70"
                style={{ color: "var(--accent)" }}
              >
                Clear all
              </button>
            )}
          </div>

          {/* Body */}
          <div className="flex-1 overflow-y-auto scrollbar-thin">
            {!hasUnread && (
              <div className="px-4 py-8 text-center">
                <Bell size={18} style={{ color: "var(--text-dim)", margin: "0 auto" }} />
                <p className="mt-2 text-[12px]" style={{ color: "var(--text-muted)" }}>
                  No new notifications.
                </p>
                <p className="mt-1 text-[10px]" style={{ color: "var(--text-dim)" }}>
                  Reminders + task results from Smara land here when Telegram isn't connected.
                </p>
              </div>
            )}

            {items.map((n) => (
              <div
                key={n.id}
                className="px-4 py-2.5 flex items-start gap-2 group transition-all"
                style={{ borderBottom: "1px solid var(--border-dim)" }}
              >
                <div className="flex-1 min-w-0">
                  <p
                    className="text-[13px] leading-snug"
                    style={{ color: "var(--text-primary)" }}
                  >
                    {n.message}
                  </p>
                  <p className="text-[10px] mt-1" style={{ color: "var(--text-muted)" }}>
                    {timeAgo(n.created_at)}
                  </p>
                </div>
                <button
                  onClick={() => handleAck(n.id)}
                  title="Dismiss"
                  className="shrink-0 opacity-30 group-hover:opacity-100 transition-opacity p-1 rounded"
                  style={{ color: "var(--text-muted)" }}
                >
                  <X size={12} />
                </button>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
