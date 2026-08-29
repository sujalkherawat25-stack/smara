/** Same-origin gateway for the independent Smara service.
 *
 * The old version minted a Memento control token for every request. Smara now
 * owns its own httpOnly session cookie, so this module only prefixes native
 * Smara routes and always sends browser credentials. No legacy agent bridge or
 * signing secret is present in the frontend bundle.
 */
const enabled = import.meta.env.VITE_SMARA_MODE === "true";
const apiOrigin = String(import.meta.env.VITE_SMARA_API_URL ?? "").replace(/\/$/, "");

const smaraPrefixes = [
  "/v1/auth", "/v1/chat", "/v1/attachments", "/v1/models", "/v1/conversations",
  "/v1/tasks", "/v1/research", "/v1/schedules", "/v1/executors", "/v1/integrations",
  "/v1/integration-actions", "/v1/push", "/v1/captures", "/v1/tools", "/v1/plugins",
  "/v1/cli", "/v1/account",
];

export function smaraModeEnabled(): boolean { return enabled; }

export function smaraUrl(path: string): string {
  if (/^https?:\/\//i.test(path)) return path;
  return `${enabled && apiOrigin ? apiOrigin : ""}${path}`;
}

function pathname(path: string): string {
  try { return new URL(path, window.location.origin).pathname; }
  catch { return path.split("?", 1)[0] ?? path; }
}

export function isSmaraPath(path: string): boolean {
  const value = pathname(path);
  return smaraPrefixes.some((prefix) => value === prefix || value.startsWith(`${prefix}/`));
}

function withJsonHeaders(init: RequestInit): RequestInit {
  const headers = new Headers(init.headers);
  const isMultipart = typeof FormData !== "undefined" && init.body instanceof FormData;
  if (!headers.has("Content-Type") && init.body !== undefined && !isMultipart) headers.set("Content-Type", "application/json");
  return { ...init, headers, credentials: "include" };
}

/** Route native Smara calls to the Smara API and preserve cookie auth. */
export async function smaraFetch(path: string, init: RequestInit = {}): Promise<Response> {
  if (!enabled || !isSmaraPath(path)) return fetch(path, { ...init, credentials: init.credentials ?? "include" });
  return fetch(smaraUrl(path), withJsonHeaders(init));
}

export async function smaraStream(path: string, body: unknown, signal?: AbortSignal): Promise<Response> {
  return smaraFetch(path, { method: "POST", headers: { Accept: "text/event-stream" }, body: JSON.stringify(body), signal });
}

/** Kept as a no-op compatibility export for desktop code written pre-cutover. */
export function resetSmaraToken(): void { /* native cookie auth has no client token */ }
