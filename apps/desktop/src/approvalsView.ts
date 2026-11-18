/** Pure helpers for the Approvals inbox: one place for everything that waits for the owner on this PC. */
export type HostedApproval = { id: string; tool: string; card: string; step: Record<string, string>; site_choice: boolean; asked_at: string | null };
export type HostedStatus = { connected: boolean; shop: string; server: string };
export type HostedList = HostedStatus & { approvals: HostedApproval[] };
export type Outcome = "done" | "already" | "chat_only";

export type SharingStatus = { connected: boolean; shop: string; enabled: boolean };

/** What the memory-sharing switch tells the owner, in plain words. */
export function sharingText(status: SharingStatus | null): string {
  if (!status || !status.connected) return "Connect this PC to your Smara above to choose whether memory is shared.";
  return status.enabled
    ? `On for ${status.shop}. Notes from your chats here are shared with Smara, kept apart and marked unverified. Passwords, keys, card numbers and OTPs are removed first.`
    : "Off. Nothing from this PC's chats is shared with Smara.";
}

export const TOOL_WORDS: Record<string, string> = {
  browser_step: "Browser on this PC", pc_files: "Smara Shared folder", hand_off_to_owner: "Your turn",
};

export function toolLabel(tool: string): string {
  return TOOL_WORDS[tool] ?? tool.replace(/_/g, " ");
}

export function outcomeText(outcome: Outcome, choice: "approve" | "site" | "reject"): string {
  if (outcome === "already") return "Already answered (in chat or on another device).";
  if (outcome === "chat_only") return "This one can be answered in chat only.";
  return choice === "reject" ? "Rejected. Smara will not do it." : choice === "site" ? "Approved, and this site is allowed for 15 minutes." : "Approved. Smara carries on.";
}

export function askedLabel(iso: string | null, now: number): string {
  if (!iso) return "";
  const seconds = Math.max(0, Math.round((now - Date.parse(iso)) / 1000));
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
  return `${Math.round(seconds / 3600)} h ago`;
}

/** Total waiting, for the toolbar badge. */
export function waitingCount(hosted: number, tasks: number, chat: number): number {
  return Math.max(0, hosted) + Math.max(0, tasks) + Math.max(0, chat);
}

export function codeLooksRight(code: string): boolean {
  return /^[A-Za-z0-9]{4}-?[A-Za-z0-9]{4}$/.test(code.trim());
}
