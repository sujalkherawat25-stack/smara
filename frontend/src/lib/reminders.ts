/**
 * lib/reminders.ts — frontend API helpers for the F5 reminders system.
 *
 * Mirrors lib/auth.ts: every call uses `credentials: "include"` so the
 * httpOnly mem_session cookie travels with the request.
 *
 * The agent's `set_reminder` tool inserts reminders during a chat — the
 * endpoints below are for viewing + cancelling. (POST is exposed too in
 * case we add a "create reminder" form later.)
 */

export interface Reminder {
  id: string;
  message: string;
  fire_at: string;          // ISO-8601
  channel: string | null;
  fired: boolean;
  created_at: string;       // ISO-8601
}

const COMMON: RequestInit = {
  credentials: "include",
  headers: { "Content-Type": "application/json" },
};

/** List the caller's reminders. By default only upcoming (fired=false). */
export async function fetchReminders(includeFired = false): Promise<Reminder[]> {
  const url = `/v1/memento/reminders${includeFired ? "?include_fired=true" : ""}`;
  let r: Response;
  try {
    r = await fetch(url, { ...COMMON, method: "GET" });
  } catch (e) {
    console.error("[reminders] fetch network error:", e);
    return [];
  }
  if (!r.ok) {
    if (r.status === 503) {
      console.warn("[reminders] backend reminders disabled (503)");
    } else if (r.status === 404) {
      console.error("[reminders] endpoint 404 — backend running old image?");
    } else if (r.status !== 401) {
      console.error("[reminders] HTTP", r.status, await r.text().catch(() => ""));
    }
    return [];
  }
  return (await r.json()) as Reminder[];
}

/** Cancel a reminder. Returns true on success. */
export async function deleteReminder(reminderId: string): Promise<boolean> {
  const r = await fetch(`/v1/memento/reminders/${encodeURIComponent(reminderId)}`, {
    ...COMMON,
    method: "DELETE",
  });
  return r.ok;
}
