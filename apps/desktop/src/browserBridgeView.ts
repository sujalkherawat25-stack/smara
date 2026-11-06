/** Presentation logic for the Browser panel. No imports: unit-tested directly. The helper (munim_bridge.py) owns the pairing and the browser. */
export type Heartbeat = { last_poll_at: number; last_step: string; last_step_at: number } | null;
export type BridgeState = { paired: boolean; server: string; shop: string; running: boolean; startup: boolean; paused: boolean; log: string[]; default_server: string; status: Heartbeat; now: number };
export type Components = { python: boolean; modules: boolean; browsers: { brave: boolean; chrome: boolean; edge: boolean } };
export type LogRow = { id: string; time: string; message: string; level: "info" | "error" };

export const MAX_LOG_ROWS = 60;
export const SITE_GRANT_MS = 15 * 60_000;

const ERROR_WORDS = /crash|disconnected|unpaired|failed|no brave|stopped:|error|could not/i;
/** bridge.log lines look like "2026-11-05 01:50:56 message". */
export function parseLog(lines: string[]): LogRow[] {
  return lines.map((line, index) => {
    const match = /^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}) (.*)$/.exec(line);
    const message = (match ? match[3] : line).trim();
    return { id: `${index}-${line.length}`, time: match ? match[2] : "", message, level: ERROR_WORDS.test(message) && !/^step .*: ok/.test(message) ? "error" as const : "info" as const };
  }).slice(-MAX_LOG_ROWS).reverse();
}
const ROLE_WORDS: Record<string, string> = { button: "button", link: "link", textbox: "box", searchbox: "search box", combobox: "list", checkbox: "tick box", radio: "option", tab: "tab", menuitem: "menu item", option: "option" };
/** '[31] button "Add to cart"' (what the helper logs now) -> 'the "Add to cart" button'; older selectors pass through. */
export function controlName(target: string): string {
  const m = /^\[\d+\] (\S+) "(.*)"/.exec(target.trim());
  if (!m) return target;
  const kind = ROLE_WORDS[m[1]] ?? "";
  if (!m[2]) return `a ${kind || "control"}`;
  return kind ? `the "${m[2]}" ${kind}` : `"${m[2]}"`;
}
export function friendlyLog(message: string): string {
  const step = /^step (\S+)(?: (.*))? on ([^\s:]+): (ok|error|refused)(?:: (.*)| \((.*)\))?$/.exec(message);
  if (step) {
    const [, action, rawTarget, host, outcome, errorText, title] = step;
    const selector = rawTarget ? controlName(rawTarget) : "";
    const verbs: Record<string, string> = { click: "Clicked", hover: "Pointed at", select: "Picked from", fill: "Typed into" };
    const what = action === "navigate" ? `Opened ${host}` : action === "read" ? `Read ${host}` : action === "text" ? `Read the text of ${host}` : verbs[action] ? `${verbs[action]}${selector ? ` ${selector}` : action === "fill" ? " a field" : ""} on ${host}` : `${action} on ${host}`;
    return outcome === "ok" ? `${what}${title ? ` — ${title}` : ""}` : `${what} failed${errorText ? `: ${errorText}` : ""}`;
  }
  if (/^step .*refused: paused/i.test(message)) return "A step was refused because Smara is paused";
  if (/^opened /i.test(message)) return `Opened your browser (${message.replace(/^opened /i, "").split(/[\\/]/).pop()})`;
  if (/closing the browser after being idle/i.test(message)) return "Closed the browser after a quiet period";
  if (/is on for/i.test(message)) return message.replace(/ Press Ctrl\+C to stop\./, "");
  if (/disconnected from smara/i.test(message)) return "This PC was unpaired from Smara. Pair again to continue.";
  return message;
}

/** The helper polls about every 25 s; it counts as online while its last poll is under this old. */
export const ONLINE_WITHIN_S = 60;
export type Connection = "unpaired" | "stopped" | "paused" | "online" | "offline" | "problem";
export function connection(state: Pick<BridgeState, "paired" | "running" | "paused" | "log" | "status" | "now">): Connection {
  if (!state.paired) return "unpaired";
  if (!state.running) return "stopped";
  if (state.paused) return "paused";
  const polled = state.status?.last_poll_at ?? 0;
  if (!polled || state.now - polled > ONLINE_WITHIN_S) return "offline";
  const last = parseLog(state.log)[0];
  return last && last.level === "error" && !/^step .*: ok/.test(last.message) ? "problem" : "online";
}
export function connectionText(value: Connection): string {
  return {
    unpaired: "Not paired", stopped: "Stopped — Smara cannot use your browser", paused: "Paused — Smara will not touch your browser",
    online: "On — Smara can use your browser when it needs to", offline: "Running, but no contact with the Smara server yet",
    problem: "On, but the last step reported a problem",
  }[value];
}

export type Missing = "python" | "modules" | "browser";
export function missingComponents(c: Components): Missing[] {
  if (!c.python) return ["python"];
  const result: Missing[] = [];
  if (!c.modules) result.push("modules");
  if (!c.browsers.brave && !c.browsers.chrome && !c.browsers.edge) result.push("browser");
  return result;
}
export function missingText(missing: Missing[]): string {
  if (missing.includes("python")) return "Python 3 is not installed. Install it once from python.org (tick “Add python.exe to PATH”), then come back here.";
  const parts = [];
  if (missing.includes("modules")) parts.push("the browser components (a one-click install)");
  if (missing.includes("browser")) parts.push("Brave, Chrome or Edge");
  return parts.length ? `Smara still needs ${parts.join(" and ")}.` : "";
}
/** Start the helper on its own only when it can work: paired, not running, nothing missing, and not tried again too soon. */
export function shouldEnsure(state: Pick<BridgeState, "paired" | "running">, missing: Missing[], lastTriedMs: number, now: number): boolean {
  return state.paired && !state.running && missing.length === 0 && now - lastTriedMs > 30_000;
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
