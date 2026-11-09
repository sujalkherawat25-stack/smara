/** The hosted-Smara chip and panel text, from the browser helper's status snapshot (browser_bridge.rs `snapshot`). */
/** `status` is the helper's own status.json; only the fields the panel shows are typed here. */
export type HelperSnapshot = { paired?: boolean; running?: boolean; paused?: boolean; shop?: string; server?: string; status?: { last_poll?: number } | null } | null | undefined;

export function hostedStatus(snapshot: HelperSnapshot): { label: string; tone: "ok" | "warn" | "off" } {
  if (!snapshot) return { label: "Hosted: checking…", tone: "off" };
  if (!snapshot.paired) return { label: "Hosted: not paired", tone: "off" };
  if (snapshot.paused) return { label: "Hosted: paused", tone: "warn" };
  if (snapshot.running) return { label: `Hosted: connected${snapshot.shop ? ` to ${snapshot.shop}` : ""}`, tone: "ok" };
  return { label: "Hosted: offline", tone: "warn" };
}
