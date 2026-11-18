/** Pure helpers for the Activity log page (the owner's local audit of what agents did on this PC). */
export type AuditEntry = {
  id: string; ts: number; source: "desktop" | "bridge"; task: string; kind: string; action: string;
  target: string; detail: unknown; outcome: string; approval: string; screenshot: string;
};
export type AuditDay = { day: string; entries: number; bytes: number };

const ACTION_WORDS: Record<string, string> = {
  browser_navigate: "Opened a page", browser_navigate_back: "Went back", browser_snapshot: "Looked at the page",
  browser_click: "Clicked", browser_type: "Typed", browser_fill_form: "Filled a form", browser_press_key: "Pressed a key",
  browser_select_option: "Chose an option", browser_hover: "Pointed at", browser_drag: "Dragged", browser_drop: "Dropped",
  browser_tabs: "Tabs", browser_file_upload: "Uploaded a file", browser_take_screenshot: "Took a screenshot",
  browser_close: "Closed the browser", download_saved: "Downloaded a file",
  navigate: "Opened a page", read: "Read the page", text: "Read the page text", click: "Clicked", fill: "Typed",
  select: "Chose an option", press: "Pressed a key", scroll: "Scrolled", back: "Went back", hover: "Pointed at",
  tabs: "Listed tabs", tab: "Switched tab", close_tab: "Closed a tab", download: "Downloaded a file", upload: "Uploaded a file",
  computer_windows: "Listed app windows", computer_launch: "Started an app", computer_observe: "Looked at an app",
  computer_act: "Acted in an app", computer_act_confirmed: "Acted in an app (approved)", computer_open_url: "Opened a page",
  excel_live: "Worked in Excel", excel_save: "Saved an Excel file", tally_read: "Read from Tally",
  approved: "You approved", declined: "You declined",
};

export function actionLabel(entry: Pick<AuditEntry, "action">): string {
  return ACTION_WORDS[entry.action] ?? entry.action.replace(/_/g, " ");
}

export function whoLabel(entry: Pick<AuditEntry, "source" | "kind">): string {
  if (entry.kind === "approval") return "You";
  return entry.source === "bridge" ? "Smara (Telegram)" : "Smara Desktop";
}

export function failed(entry: Pick<AuditEntry, "outcome">): boolean {
  return /^(error|failed)\b/i.test(entry.outcome.trim());
}

export function timeLabel(ts: number): string {
  const d = new Date(ts * 1000);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}:${String(d.getSeconds()).padStart(2, "0")}`;
}

export function sizeLabel(bytes: number): string {
  return bytes >= 1_000_000 ? `${(bytes / 1_000_000).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1000))} KB`;
}

/** Matches when every word of the query is in the row's visible text. */
export function matches(entry: AuditEntry, query: string): boolean {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return true;
  const hay = [actionLabel(entry), entry.action, entry.target, entry.outcome, entry.kind, whoLabel(entry)].join(" ").toLowerCase();
  return words.every(word => hay.includes(word));
}

export function filterEntries(entries: AuditEntry[], kind: string, query: string): AuditEntry[] {
  return entries.filter(entry => (kind === "all" || entry.kind === kind) && matches(entry, query));
}
