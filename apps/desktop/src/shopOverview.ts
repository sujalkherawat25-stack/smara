/**
 * The paired shop's numbers on the Hosted panel, from the helper's saved copy (browser_bridge.rs `snapshot().overview`).
 * These are the same totals the owner sees in chat (Telegram or WhatsApp): the server is the single source, and the
 * helper refreshes its copy about once a minute. Totals only; no customer names.
 */
export type ShopOverview = {
  shop?: string;
  dues?: { open?: number; open_paise?: number; overdue?: number; overdue_paise?: number };
  payables?: { open?: number; open_paise?: number };
  waiting_for_owner?: number;
  channels?: { telegram_linked?: boolean; whatsapp_window_open?: boolean; last_used?: string };
  tally_last_sync_at?: string | null;
  fetched_at?: number;
} | null | undefined;

export type OverviewLine = { label: string; tone: "ok" | "warn" | "off" };

/** A copy older than this is shown as out of date (the helper refreshes every minute while the PC is connected). */
export const STALE_AFTER_S = 15 * 60;

/** Rupees the Indian way: 1,23,456 (no paise). */
export function rupees(paise: number): string {
  const whole = Math.round((paise || 0) / 100);
  const digits = String(Math.abs(whole));
  const last3 = digits.slice(-3);
  const rest = digits.slice(0, -3).replace(/\B(?=(\d{2})+(?!\d))/g, ",");
  return `${whole < 0 ? "-" : ""}₹${rest ? `${rest},` : ""}${last3}`;
}

function ago(seconds: number): string {
  if (seconds < 90) return "just now";
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)} h ago`;
  return `${Math.round(seconds / 86400)} days ago`;
}

/** The rows under "Your shop". Empty when this PC is not paired or the helper has not fetched anything yet. */
export function overviewLines(overview: ShopOverview, nowSeconds: number): OverviewLine[] {
  if (!overview || !overview.fetched_at) return [];
  const lines: OverviewLine[] = [];
  const dues = overview.dues ?? {};
  const overdue = dues.overdue ?? 0;
  lines.push({ label: `To collect: ${rupees(dues.open_paise ?? 0)} (${dues.open ?? 0} open)`, tone: "off" });
  lines.push(overdue > 0
    ? { label: `Overdue: ${rupees(dues.overdue_paise ?? 0)} (${overdue})`, tone: "warn" }
    : { label: "Overdue: none", tone: "ok" });
  const payables = overview.payables ?? {};
  if ((payables.open ?? 0) > 0) lines.push({ label: `To pay: ${rupees(payables.open_paise ?? 0)} (${payables.open} bills)`, tone: "off" });
  const waiting = overview.waiting_for_owner ?? 0;
  const where = overview.channels?.last_used === "telegram" ? "Telegram" : "chat";
  lines.push(waiting > 0
    ? { label: `${waiting} waiting for your approval in ${where}`, tone: "warn" }
    : { label: "Nothing waiting for your approval", tone: "ok" });
  lines.push(overview.channels?.telegram_linked
    ? { label: "Your Telegram: linked", tone: "ok" }
    : { label: "Your Telegram: not linked", tone: "warn" });
  if (overview.tally_last_sync_at) {
    const at = Date.parse(overview.tally_last_sync_at) / 1000;
    if (Number.isFinite(at)) lines.push({ label: `Tally synced ${ago(nowSeconds - at)}`, tone: nowSeconds - at > 2 * 86400 ? "warn" : "ok" });
  } else {
    lines.push({ label: "Tally: not synced yet", tone: "off" });
  }
  const age = nowSeconds - overview.fetched_at;
  if (age > STALE_AFTER_S) lines.push({ label: `Numbers last updated ${ago(age)}`, tone: "warn" });
  return lines;
}
