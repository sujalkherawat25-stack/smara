/** Pure helpers for the Models settings. */
/** Blank context window means "use the default"; anything else must be a whole number of tokens. */
export function modelPayload(model: Record<string, unknown>) {
  const { context_window, ...rest } = model;
  const value = String(context_window ?? "").trim();
  return value ? { ...rest, context_window: Number(value) } : rest;
}
/** A model needs a profile ID; people type a display name, so derive one when it is left blank. */
export function profileId(label: string, existing: string[]): string {
  const base = label.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "") || "model";
  let id = base, n = 2;
  while (existing.includes(id)) id = `${base}_${n++}`;
  return id;
}
/** Which sites Smara may browse: "*" means any public website; otherwise the exact list. */
export const ANY_SITE = "*";
export function browsesAnySite(origins: string): boolean {
  return origins.split(/\s+/).includes(ANY_SITE);
}
