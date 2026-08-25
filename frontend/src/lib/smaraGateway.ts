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

async function controlToken(): Promise<string> {
  const now = Date.now();
  if (cachedToken && now < tokenExpiresAt - 15_000) return cachedToken;
  if (tokenPromise) return tokenPromise;

  tokenPromise = fetch(tokenPath, { credentials: "include" })
    .then(async (response) => {
      if (!response.ok) throw new Error(`Smara session bridge failed (${response.status})`);
      const payload = await response.json() as { token?: string; expires_in?: number };
      if (!payload.token) throw new Error("Smara session bridge returned no token.");
      cachedToken = payload.token;
      tokenExpiresAt = Date.now() + Math.max(30, payload.expires_in ?? 300) * 1000;
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
  if (!enabled) return fetch(path, init);
  const token = await controlToken();
  return fetch(smaraUrl(path), withJsonHeaders(init, token));
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
