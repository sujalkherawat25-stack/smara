/**
 * Transitional browser bridge for the Memento UI running on Smara.
 *
 * The browser never receives the Smara signing secret.  When the UI is
 * pointed at a Smara deployment, the existing authenticated web session
 * mints a short-lived control token and this module uses it for Smara API
 * calls.  In the final same-origin deployment, set VITE_SMARA_API_URL to an
 * empty value and the gateway can issue the same token without CORS.
 *
 * This file is deliberately UI-only: it imports no MemoryOS code and does
 * not create a second agent or memory store.
 */

const enabled = import.meta.env.VITE_SMARA_MODE === "true";
const apiOrigin = String(import.meta.env.VITE_SMARA_API_URL ?? "").replace(/\/$/, "");
const tokenPath = String(import.meta.env.VITE_SMARA_CONTROL_TOKEN_PATH ?? "/v1/auth/control-token");

// Only these API families belong to the independent Smara service today.
// The reused Memento shell still owns memory, graph, stats, uploads, and its
// remaining /v1/memento/* routes. Keeping the split here makes the migration
// safe: enabling Smara mode cannot silently turn an old panel into a 404.
const smaraPrefixes = [
  "/v1/chat",
  "/v1/attachments",
  "/v1/models",
  "/v1/conversations",
  "/v1/tasks",
  "/v1/research",
  "/v1/schedules",
  "/v1/executors",
  "/v1/integrations",
  "/v1/integration-actions",
  "/v1/push",
  "/v1/captures",
  "/v1/tools",
  "/v1/plugins",
  "/v1/cli",
  "/v1/account",
];

let cachedToken: string | null = null;
let tokenExpiresAt = 0;
let tokenPromise: Promise<string> | null = null;

export function smaraModeEnabled(): boolean {
  return enabled;
}

export function smaraUrl(path: string): string {
  if (/^https?:\/\//i.test(path)) return path;
  return `${apiOrigin}${path}`;
}

function pathname(path: string): string {
  try { return new URL(path, window.location.origin).pathname; }
  catch { return path.split("?", 1)[0] ?? path; }
}

/** Whether a request belongs to the Smara API during the staged migration. */
export function isSmaraPath(path: string): boolean {
  const value = pathname(path);
  return smaraPrefixes.some((prefix) => value === prefix || value.startsWith(`${prefix}/`));
}

async function controlToken(): Promise<string> {
  const now = Date.now();
  if (cachedToken && now < tokenExpiresAt - 15_000) return cachedToken;
  if (tokenPromise) return tokenPromise;

  // The bridge endpoint is a state-changing mint operation. Keep the
  // session cookie on this same-origin POST; using GET here returned 405 and
  // left the Smara panels looking disconnected even for signed-in users.
  tokenPromise = fetch(tokenPath, {
    method: "POST",
    credentials: "include",
    headers: { Accept: "application/json" },
  })
    .then(async (response) => {
      if (!response.ok) throw new Error(`Smara session bridge failed (${response.status})`);
      const payload = await response.json() as {
        token?: string;
        // The backend wire contract uses the explicit *_seconds name. Keep
        // the older alias readable for already deployed auth services during
        // the staged migration.
        expires_in_seconds?: number;
        expires_in?: number;
      };
      if (!payload.token) throw new Error("Smara session bridge returned no token.");
      cachedToken = payload.token;
      const ttl = payload.expires_in_seconds ?? payload.expires_in ?? 60;
      tokenExpiresAt = Date.now() + Math.max(30, Math.min(ttl, 300)) * 1000;
      return payload.token;
    })
    .finally(() => { tokenPromise = null; });
  return tokenPromise;
}

function withJsonHeaders(init: RequestInit, token: string): RequestInit {
  const headers = new Headers(init.headers);
  if (!headers.has("Content-Type") && init.body !== undefined) headers.set("Content-Type", "application/json");
  headers.set("Authorization", `Bearer ${token}`);
  return { ...init, headers, credentials: "omit" };
}

/** Fetch an API route, using the Memento session only while bridge mode is on. */
export async function smaraFetch(path: string, init: RequestInit = {}): Promise<Response> {
  // Legacy Memento/MemoryOS routes continue to use the current origin and its
  // httpOnly session. Only Smara-native routes need a bridge token and API
  // origin, so the old panels remain usable while migration proceeds.
  if (!enabled || !isSmaraPath(path)) {
    return fetch(path, { ...init, credentials: init.credentials ?? "include" });
  }
  const token = await controlToken();
  const response = await fetch(smaraUrl(path), withJsonHeaders(init, token));
  // A deploy, secret rotation, or revoked bridge token can invalidate a
  // cached assertion before its advertised TTL. Refresh once on auth failure
  // so a normal retry is transparent to the UI. Do not loop indefinitely.
  if (response.status !== 401 && response.status !== 403) return response;
  resetSmaraToken();
  const refreshed = await controlToken();
  return fetch(smaraUrl(path), withJsonHeaders(init, refreshed));
}

/** Raw streaming request for the chat SSE endpoint. */
export async function smaraStream(path: string, body: unknown, signal?: AbortSignal): Promise<Response> {
  return smaraFetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(body),
    signal,
  });
}

/** Clear a rejected token so the next request performs a fresh bridge mint. */
export function resetSmaraToken(): void {
  cachedToken = null;
  tokenExpiresAt = 0;
}
