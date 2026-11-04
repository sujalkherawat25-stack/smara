/** Presentation logic for the Browser panel. No imports: unit-tested directly. Rust owns pairing, the token and the process. */
export type BridgeConfig = { server: string; shop: string; device_name: string; browser: string; attach: string; profile: string; autostart: boolean };
export type BridgeEvent = {
  kind: string; ts: number; job_id?: string; action?: string; host?: string; url?: string; selector?: string; value?: string;
  title?: string; ok?: boolean; error?: string | null; ms?: number; paused?: boolean; message?: string; status?: number;
  silent?: boolean; code?: number | null; browser?: string; unpaired?: boolean;
};
export type BridgeState = {
  paired: boolean; config: BridgeConfig; running: boolean; paused: boolean; last_poll_ok_ms: number; last_error: string;
  events: BridgeEvent[]; default_server: string; default_profile: string;
};
export type FeedRow = { id: string; ts: number; action: string; label: string; host: string; title: string; status: "running" | "ok" | "error"; error: string; ms: number };

/** The server counts a bridge as online for 45 s after its last poll; match that window. */
export const ONLINE_WITHIN_MS = 45_000;
export const SITE_GRANT_MS = 15 * 60_000;
export const MAX_FEED = 200;

export type Connection = "unpaired" | "stopped" | "starting" | "online" | "offline" | "paused";
export function connection(state: Pick<BridgeState, "paired" | "running" | "paused" | "last_poll_ok_ms">, now: number): Connection {
  if (!state.paired) return "unpaired";
  if (!state.running) return "stopped";
  if (state.paused) return "paused";
  if (!state.last_poll_ok_ms) return "starting";
  return now - state.last_poll_ok_ms <= ONLINE_WITHIN_MS ? "online" : "offline";
}
export function connectionText(value: Connection): string {
  return {
    unpaired: "Not paired", stopped: "Stopped", starting: "Starting…", online: "Online — Smara can use your browser",
    offline: "Offline — no contact with the Smara server", paused: "Paused — Smara will not touch your browser",
  }[value];
}

export function stepLabel(event: Pick<BridgeEvent, "action" | "url" | "selector" | "value" | "host">): string {
  const target = event.selector ? ` ${event.selector}` : "";
  switch (event.action) {
    case "navigate": return `Open ${event.host || event.url || "page"}`;
    case "read": return "Read page";
    case "click": return `Click${target}`;
    case "fill": return `Type into${target}${event.value ? ` “${event.value}”` : ""}`;
    case "press": return `Press ${event.value || "Enter"}`;
    default: return event.action ? String(event.action) : "Step";
  }
}

/** Pair each step with its result and keep failures visible; non-step events become notes. */
export function buildFeed(events: BridgeEvent[]): FeedRow[] {
  const rows: FeedRow[] = [];
  const byJob = new Map<string, FeedRow>();
  for (const event of events) {
    if (event.kind === "step" && event.job_id) {
      const row: FeedRow = { id: event.job_id, ts: event.ts, action: event.action || "", label: stepLabel(event), host: event.host || "", title: "", status: "running", error: "", ms: 0 };
      byJob.set(event.job_id, row); rows.push(row);
    } else if (event.kind === "result" && event.job_id) {
      const row = byJob.get(event.job_id);
      if (!row) continue;
      row.status = event.ok ? "ok" : "error"; row.error = event.error || ""; row.title = event.title || ""; row.ms = event.ms || 0;
      if (event.host) row.host = event.host;
    } else if (["fatal", "exited", "stderr"].includes(event.kind) || (event.kind === "poll" && event.ok === false)) {
      const message = event.kind === "exited" ? `Bridge stopped${event.code ? ` (code ${event.code})` : ""}`
        : event.kind === "poll" ? `Cannot reach server${event.status ? ` (HTTP ${event.status})` : ""}` : event.message || "Bridge error";
      rows.push({ id: `${event.kind}-${event.ts}-${rows.length}`, ts: event.ts, action: event.kind, label: message, host: "", title: "", status: event.kind === "exited" ? "ok" : "error", error: event.kind === "exited" ? "" : message, ms: 0 });
    }
  }
  return rows.slice(-MAX_FEED);
}

/** A step that never got a result while the bridge is stopped did not finish. */
export function settleFeed(rows: FeedRow[], running: boolean): FeedRow[] {
  return running ? rows : rows.map(row => row.status === "running" ? { ...row, status: "error" as const, error: "Stopped before this step finished" } : row);
}

export function mergeEvents(current: BridgeEvent[], event: BridgeEvent): BridgeEvent[] {
  if (event.kind === "poll" && event.ok) return current;          // heartbeat only; it moves the online light, not the feed
  if (current.some(e => e.ts === event.ts && e.kind === event.kind && e.job_id === event.job_id)) return current;   // a repeated delivery
  const next = [...current, event];
  return next.length > MAX_FEED * 2 ? next.slice(-MAX_FEED * 2) : next;
}

export function shouldAutoStart(state: Pick<BridgeState, "paired" | "running" | "config">, starting: boolean, already: boolean): boolean {
  return !!state.config.autostart && state.paired && !state.running && !starting && !already;
}

export function validatePairInput(server: string, code: string): string {
  const value = server.trim().replace(/\/+$/, "");
  const match = /^(https?):\/\/([^/:@\s?#]+)/i.exec(value);
  if (!match) return "Enter the full server address, for example https://ai.syntarus.com/munim";
  const local = ["localhost", "127.0.0.1"].includes(match[2].toLowerCase());
  if (match[1].toLowerCase() === "http" && !local) return "The server address must start with https://";
  if (code.replace(/[^a-z0-9]/gi, "").length !== 8) return "The pairing code looks like ABCD-EFGH. Send “pair browser” to Smara in chat to get one.";
  return "";
}

export function describeBrowser(config: Pick<BridgeConfig, "browser" | "attach">): string {
  if (config.attach) return `Attached to the browser at ${config.attach}`;
  return { brave: "Brave", chrome: "Chrome", edge: "Edge", auto: "First browser found" }[config.browser as "brave"] ?? config.browser;
}

// ---- approvals: the same card the chat shows. Chat stays authoritative; first answer wins. -------------------
export type ApprovalCard = { id: string; page: string; host: string; element: string; value: string; action: string; asked_at: number };
export type ApprovalChoice = "approve" | "site" | "reject";
export const APPROVAL_BUTTONS: { choice: ApprovalChoice; label: string }[] = [
  { choice: "approve", label: "Approve" }, { choice: "site", label: "Approve this site (15 min)" }, { choice: "reject", label: "Reject" },
];
/** Cards still waiting: not answered here, and not older than the server keeps a session waiting. */
export function pendingApprovals(cards: ApprovalCard[], answered: Set<string>, now: number, maxAgeMs = 24 * 60 * 60_000): ApprovalCard[] {
  return cards.filter(card => !answered.has(card.id) && now - card.asked_at <= maxAgeMs);
}
export function siteGrantUntil(now: number): number { return now + SITE_GRANT_MS; }
export function describeApproval(card: ApprovalCard): string {
  const verb = card.action === "fill" ? "type into" : card.action === "click" ? "click" : card.action;
  return `Smara wants to ${verb} ${card.element || "an element"} on ${card.host || card.page}${card.action === "fill" && card.value ? ` — “${card.value}”` : ""}`;
}
/** The server answers “already handled” when chat replied first; treat that as success, not an error. */
export function approvalOutcome(status: number): "done" | "already" | "unsupported" | "error" {
  if (status >= 200 && status < 300) return "done";
  if (status === 409 || status === 404) return "already";
  if (status === 405 || status === 501) return "unsupported";
  return "error";
}
