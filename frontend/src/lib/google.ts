/**
 * lib/google.ts — frontend helpers for the Google OAuth grant (Gmail + Calendar).
 *
 * Flow:
 *   1. fetchGoogleStatus() → tells the UI whether available + connected + features
 *   2. startGoogleConnect() → returns Google consent URL; frontend redirects there
 *   3. Google bounces back to /v1/auth/google/callback which redirects to
 *      /?google=connected (or declined/error) — App.tsx already handles the toast
 *   4. disconnectGoogle() → revoke + drop the grant row
 */

export interface GoogleStatus {
  available: boolean;          // false when GOOGLE_CLIENT_SECRET unset server-side
  connected: boolean;
  features: string[];          // e.g. ["gmail", "calendar"]
  granted_at: string | null;
}

const COMMON: RequestInit = {
  credentials: "include",
  headers: { "Content-Type": "application/json" },
};

export async function fetchGoogleStatus(): Promise<GoogleStatus> {
  try {
    const r = await fetch("/v1/auth/google/status", { ...COMMON, method: "GET" });
    if (!r.ok) {
      if (r.status === 401) return { available: false, connected: false, features: [], granted_at: null };
      console.error("[google] status HTTP", r.status, await r.text().catch(() => ""));
      return { available: false, connected: false, features: [], granted_at: null };
    }
    const data = await r.json();
    return {
      available: !!data.available,
      connected: !!data.connected,
      features: data.features ?? [],
      granted_at: data.granted_at ?? null,
    };
  } catch (e) {
    console.error("[google] status network error:", e);
    return { available: false, connected: false, features: [], granted_at: null };
  }
}

/** Mint a consent state token + return Google's consent URL. */
export async function startGoogleConnect(
  features: ("gmail" | "calendar")[] = ["gmail", "calendar"],
): Promise<{ auth_url: string }> {
  const q = encodeURIComponent(features.join(","));
  const r = await fetch(`/v1/auth/google/connect?features=${q}`, { ...COMMON, method: "GET" });
  if (!r.ok) {
    const err = await r.json().catch(() => ({}));
    throw new Error(err.detail ?? `google connect failed (${r.status})`);
  }
  return await r.json();
}

export async function disconnectGoogle(): Promise<boolean> {
  const r = await fetch("/v1/auth/google/connect", { ...COMMON, method: "DELETE" });
  return r.ok;
}
