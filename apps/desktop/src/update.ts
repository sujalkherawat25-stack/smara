export type UpdateState = "idle" | "installing" | "failed";

/** The update bar's one line. */
export function updateLine(state: UpdateState, version: string, percent = 0): string {
  if (state === "installing") return `Updating Smara Desktop to ${version}${percent > 0 ? ` (${Math.min(100, Math.round(percent))}%)` : ""}… it restarts by itself.`;
  if (state === "failed") return `The update to ${version} did not install. Try again, or download it from Smara.`;
  return `Smara Desktop ${version} is ready.`;
}

/** Installing restarts the app, so it waits while a task, approval or save is in progress. */
export function canInstall(state: UpdateState, busy: boolean): boolean {
  return state !== "installing" && !busy;
}
