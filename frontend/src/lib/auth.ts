/**
 * lib/auth.ts — frontend auth API helpers.
 *
 * Every call uses `credentials: "include"` so the httpOnly mem_session
 * cookie travels with the request. The cookie is set by the backend on
 * /v1/auth/google and cleared by /v1/auth/logout.
 *
 * No tokens leave server. No localStorage. No bearer header.
 */

export interface Account {
  account_id: string;
  email: string | null;
  display_name: string | null;
  avatar_url: string | null;
  plan: "free" | "founder" | string;
  // Phase-1 onboarding: preferred_name is what Smara calls the user
  // (writer-prompt {self} substitution). onboarded_at is null until the
  // user completes the warm welcome flow on first login.
  preferred_name?: string | null;
  onboarded_at?: string | null;
  /** Private profile birthday, shown as day + month only (never birth year). */
  birthday?: string | null;
}

export interface LinkCode {
  code: string;
  bot_url: string;
  expires_in_seconds: number;
}

const COMMON: RequestInit = {
  credentials: "include",
  headers: { "Content-Type": "application/json" },
};

/** Sign in by handing a Google ID token to the backend. Backend sets cookie. */
export async function signInWithGoogle(idToken: string): Promise<Account> {
  const r = await fetch("/v1/auth/google", {
    ...COMMON,
    method: "POST",
    body: JSON.stringify({ id_token: idToken }),
  });
  if (!r.ok) {
    const err = await r.json().catch(() => ({}));
    throw new Error(err.detail ?? `sign-in failed (${r.status})`);
  }
  return (await r.json()) as Account;
}

export async function requestEmailOtp(email: string): Promise<{ message: string; expires_in_seconds: number }> {
  const r = await fetch("/v1/auth/email/request", {
    ...COMMON, method: "POST", body: JSON.stringify({ email }),
  });
  if (!r.ok) {
    const err = await r.json().catch(() => ({}));
    throw new Error(err.detail ?? `code request failed (${r.status})`);
  }
  return await r.json();
}

export async function verifyEmailOtp(email: string, code: string): Promise<Account> {
  const r = await fetch("/v1/auth/email/verify", {
    ...COMMON, method: "POST", body: JSON.stringify({ email, code }),
  });
  if (!r.ok) {
    const err = await r.json().catch(() => ({}));
    throw new Error(err.detail ?? `code verification failed (${r.status})`);
  }
  return (await r.json()) as Account;
}

/** Revoke server-side session and clear the cookie. Safe to call repeatedly. */
export async function signOut(): Promise<void> {
  await fetch("/v1/auth/logout", { ...COMMON, method: "POST" });
}

/**
 * Fetch the current account. Returns null on 401 (not signed in) so the
 * caller can route to the sign-in screen without try/catch noise.
 */
export async function fetchMe(): Promise<Account | null> {
  const r = await fetch("/v1/auth/me", { ...COMMON, method: "GET" });
  if (r.status === 401) return null;
  if (!r.ok) throw new Error(`/auth/me failed (${r.status})`);
  return (await r.json()) as Account;
}

/** Generate a 6-digit Telegram linking code. Requires an active session. */
export async function generateTelegramLinkCode(): Promise<LinkCode> {
  const r = await fetch("/v1/auth/link/telegram", { ...COMMON, method: "GET" });
  if (!r.ok) {
    const err = await r.json().catch(() => ({}));
    throw new Error(err.detail ?? `link-code generation failed (${r.status})`);
  }
  return (await r.json()) as LinkCode;
}

export interface TelegramLinkStatus {
  linked: boolean;
  linked_at: string | null;
  channel_user_preview: string | null;
}

/** Check whether the current account has a Telegram link. */
export async function fetchTelegramLinkStatus(): Promise<TelegramLinkStatus> {
  let r: Response;
  try {
    r = await fetch("/v1/auth/link/telegram/status", { ...COMMON, method: "GET" });
  } catch (e) {
    console.error("[telegram] status fetch network error:", e);
    return { linked: false, linked_at: null, channel_user_preview: null };
  }
  if (!r.ok) {
    // 401 (no session) and 503 (F3 disabled) are expected. 404 means the
    // backend wasn't rebuilt yet — surface that loudly.
    if (r.status === 404) {
      console.error(
        "[telegram] /v1/auth/link/telegram/status returned 404 — backend " +
        "container is running old image. Rebuild + redeploy the backend.",
      );
    } else if (r.status !== 401 && r.status !== 503) {
      console.error("[telegram] status returned HTTP", r.status, await r.text().catch(() => ""));
    }
    return { linked: false, linked_at: null, channel_user_preview: null };
  }
  return (await r.json()) as TelegramLinkStatus;
}

export interface OnboardingResult {
  ok: boolean;
  preferred_name: string | null;
  onboarded_at: string;
}

/**
 * Phase-1: complete the warm welcome flow. Pass the user's preferred
 * name (what Smara should call them). Sets onboarded_at server-side so
 * future logins skip the welcome screen.
 */
export async function completeOnboarding(
  preferred_name: string | null,
  options?: { birthday?: string },
): Promise<OnboardingResult> {
  const r = await fetch("/v1/auth/me/onboarding", {
    ...COMMON,
    method: "POST",
    body: JSON.stringify({ preferred_name, ...options }),
  });
  if (!r.ok) {
    const err = await r.json().catch(() => ({}));
    throw new Error(err.detail ?? `onboarding failed (${r.status})`);
  }
  return (await r.json()) as OnboardingResult;
}

/** Disconnect Telegram from the current account. Idempotent. */
export async function unlinkTelegram(): Promise<void> {
  const r = await fetch("/v1/auth/link/telegram", { ...COMMON, method: "DELETE" });
  if (!r.ok) {
    const err = await r.json().catch(() => ({}));
    throw new Error(err.detail ?? `unlink failed (${r.status})`);
  }
}
