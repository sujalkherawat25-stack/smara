/**
 * stores/authStore.ts — single source of truth for "who is logged in?"
 *
 * Driven by the httpOnly mem_session cookie on the backend. We never
 * store the JWT here — only the public profile fields the user sees.
 *
 *   status      "loading" while we boot and call /me once.
 *               "signed_in" when /me returned an account.
 *               "signed_out" when /me returned 401 or signOut succeeded.
 *
 *   account     populated when status === "signed_in", null otherwise.
 *
 *   loadAccount()  call once on app mount. Idempotent.
 *   signIn(...)    called from SignInScreen on successful Google flow.
 *   signOut()      revokes server-side + clears the cookie + flips state.
 */

import { create } from "zustand";
import {
  type Account,
  fetchMe,
  signInWithGoogle as apiSignIn,
  requestEmailOtp as apiRequestEmailOtp,
  verifyEmailOtp as apiVerifyEmailOtp,
  signOut as apiSignOut,
  completeOnboarding as apiCompleteOnboarding,
} from "@/lib/auth";
import { isMaintenanceError } from "@/lib/httpErrors";

// "unreachable" is distinct from "signed_out": a 401 genuinely means no
// session, but a 502/503/network failure just means the server is mid-deploy.
// Treating the latter as "signed_out" would boot an already-logged-in user
// to the sign-in screen every time a redeploy catches them mid-session.
export type AuthStatus = "loading" | "signed_in" | "signed_out" | "unreachable";

// How often to re-probe /me while the server looks like it's mid-deploy.
const UNREACHABLE_RETRY_MS = 5000;

interface AuthState {
  status: AuthStatus;
  account: Account | null;
  /** Cause of the last failed sign-in, surfaced in the SignInScreen if set. */
  error: string | null;

  loadAccount: () => Promise<void>;
  signInWithGoogleToken: (idToken: string) => Promise<void>;
  requestEmailOtp: (email: string) => Promise<void>;
  verifyEmailOtp: (email: string, code: string) => Promise<void>;
  signOut: () => Promise<void>;
  /** Phase-1: capture preferred_name + flip onboarded_at server-side.
   *  Updates the cached account in-place so the welcome screen unmounts
   *  immediately and the user sees the normal chat input. */
  completeOnboarding: (preferred_name: string | null, options?: { birthday?: string }) => Promise<void>;
}

export const useAuthStore = create<AuthState>((set, get) => ({
  status: "loading",
  account: null,
  error: null,

  loadAccount: async () => {
    // Guard: don't refire if we've already determined state (loading or
    // unreachable are both "still trying" — either can call this again).
    const current = get().status;
    if (current !== "loading" && current !== "unreachable") return;
    try {
      const acct = await fetchMe();
      if (acct) {
        set({ status: "signed_in", account: acct, error: null });
      } else {
        // A genuine 401 — there's really no session, not a deploy hiccup.
        set({ status: "signed_out", account: null, error: null });
      }
    } catch (err) {
      if (isMaintenanceError(err)) {
        // Mid-deploy blip, not a real sign-out. Keep the message calm and
        // retry on our own — the user shouldn't have to hit refresh.
        set({ status: "unreachable", account: null, error: null });
        window.setTimeout(() => {
          if (get().status === "unreachable") get().loadAccount();
        }, UNREACHABLE_RETRY_MS);
        return;
      }
      // Some other failure — fall back to the sign-in screen with a soft
      // error message the user can act on (e.g. retry).
      set({
        status: "signed_out",
        account: null,
        error: err instanceof Error && err.message ? err.message : "Could not sign you in right now. Please try again.",
      });
    }
  },

  signInWithGoogleToken: async (idToken: string) => {
    try {
      const acct = await apiSignIn(idToken);
      set({ status: "signed_in", account: acct, error: null });
    } catch (err) {
      set({
        status: "signed_out",
        account: null,
        error: err instanceof Error && err.message ? err.message : "Could not sign you in right now. Please try again.",
      });
      throw err; // let the caller decide whether to show a toast
    }
  },

  requestEmailOtp: async (email: string) => {
    try {
      await apiRequestEmailOtp(email);
      set({ error: null });
    } catch (err) {
      const message = err instanceof Error ? err.message : "Could not send a sign-in code.";
      set({ error: message });
      throw err;
    }
  },

  verifyEmailOtp: async (email: string, code: string) => {
    try {
      const acct = await apiVerifyEmailOtp(email, code);
      set({ status: "signed_in", account: acct, error: null });
    } catch (err) {
      const message = err instanceof Error ? err.message : "That code is invalid or expired.";
      set({ error: message });
      throw err;
    }
  },

  signOut: async () => {
    try {
      await apiSignOut();
    } catch {
      /* server-side revoke is best-effort; cookie clears regardless */
    }
    set({ status: "signed_out", account: null, error: null });
  },

  completeOnboarding: async (preferred_name: string | null, options?: { birthday?: string }) => {
    const result = await apiCompleteOnboarding(preferred_name, options);
    const current = get().account;
    if (current) {
      set({
        account: {
          ...current,
          preferred_name: result.preferred_name,
          onboarded_at: result.onboarded_at,
        },
      });
    }
  },
}));
