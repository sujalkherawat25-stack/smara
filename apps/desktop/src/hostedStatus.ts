/**
 * The hosted-Smara chip and panel text, from the browser helper's snapshot (browser_bridge.rs `snapshot`).
 * `running` only means the helper process is alive. The helper keeps `online` true after its first successful
 * poll and never clears it, so "connected" also needs a recent `last_poll_at` (status.json, epoch seconds).
 */
export type HelperSnapshot = {
  paired?: boolean; running?: boolean; paused?: boolean; shop?: string; server?: string;
  now?: number; status?: { last_poll_at?: number; online?: boolean } | null;
} | null | undefined;

/** A successful poll this recent counts as connected. */
export const LIVE_WINDOW_S = 120;

export function hostedStatus(snapshot: HelperSnapshot): { label: string; tone: "ok" | "warn" | "off" } {
  if (!snapshot) return { label: "Hosted: checking…", tone: "off" };
  if (!snapshot.paired) return { label: "Hosted: not paired", tone: "off" };
  if (snapshot.paused) return { label: "Hosted: paused", tone: "warn" };
  if (!snapshot.running) return { label: "Hosted: offline", tone: "warn" };
  const now = snapshot.now ?? Date.now() / 1000;
  const last = snapshot.status?.last_poll_at ?? 0;
  const live = snapshot.status?.online === true && last > 0 && now - last <= LIVE_WINDOW_S;
  if (live) return { label: `Hosted: connected${snapshot.shop ? ` to ${snapshot.shop}` : ""}`, tone: "ok" };
  return { label: "Hosted: offline", tone: "warn" };
}
