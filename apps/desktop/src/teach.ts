/** Teach by showing (the Teach panel): types and small helpers for a recording of the owner's own browser steps. */

export type TeachStep = { n: number; text: string };
export type TeachState = { state: "idle" | "recording" | "stopped" | "failed"; detail: string; start_url: string; steps: TeachStep[]; count: number; lesson?: string };

/** "example.com/orders" becomes "https://example.com/orders"; anything else is left for the server to refuse. */
export function normalizeUrl(value: string): string {
  const text = value.trim();
  if (!text) return "";
  return /^[a-z][a-z0-9+.-]*:/i.test(text) ? text : `https://${text}`;
}

/** Why the Start button is off, or "" when it can go. */
export function startProblem(url: string, employee: string): string {
  if (!employee) return "Pick who you are teaching.";
  const address = normalizeUrl(url);
  if (!address) return "Type the website where the job starts.";
  return /^https?:\/\/[^\s/]+\.[^\s/]+/i.test(address) ? "" : "Type a website address, like example.com.";
}

/** One line under the buttons that says what is happening. */
export function teachLine(state: TeachState): string {
  if (state.state === "recording") return "Recording. Do the job in the window that opened, then press Done.";
  if (state.state === "failed") return state.detail || "Could not record.";
  if (state.state === "stopped" && state.count === 0) return "Nothing was recorded. Try again and click through the job.";
  if (state.state === "stopped") return `${state.count} step${state.count === 1 ? "" : "s"} recorded. Remove any you do not want, then teach.`;
  return "Pick an employee, type where the job starts, and press Start. Smara opens its browser and writes down what you do.";
}
