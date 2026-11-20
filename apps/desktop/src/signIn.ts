/** Smara Desktop sign-in: what the owner sees while the PC asks to be connected, from `hosted_login` replies. */

export type SignInState = "none" | "pending" | "asked" | "approved" | "denied" | "expired";

export type SignIn = {
  signed_in: boolean; partial?: boolean; waiting: boolean; shop: string; server: string;
  code: string; message: string; links: { telegram?: string; whatsapp?: string };
  expires_at: number; state?: SignInState;
};

export const EMPTY_SIGN_IN: SignIn = { signed_in: false, waiting: false, shop: "", server: "", code: "", message: "", links: {}, expires_at: 0 };

/** The one line under the code: what the owner should do next. */
export function waitingText(state: SignInState | undefined): string {
  if (state === "asked") return "Smara asked you to confirm in the chat. Reply YES there to finish.";
  if (state === "denied") return "You answered NO, so nothing was connected.";
  if (state === "expired") return "That sign-in timed out. Press Sign in to get a new code.";
  return "Tap a link below, send the message it opens, then reply YES when Smara asks.";
}

/** Whole minutes the code stays valid, never negative. */
export function minutesLeft(expiresAt: number, nowSeconds: number): number {
  return Math.max(0, Math.ceil((expiresAt - nowSeconds) / 60));
}

/** The sign-in is over (success or not) once the server says so. */
export function finished(state: SignInState | undefined): boolean {
  return state === "approved" || state === "denied" || state === "expired";
}

/** What the card says when signed in, from the helper chip (online, paused, offline). */
export function connectedLine(shop: string, helperLabel: string): string {
  const name = shop || "your shop";
  return /connected/i.test(helperLabel) ? `Connected to ${name}` : /paused/i.test(helperLabel) ? `Signed in to ${name} · paused` : `Signed in to ${name} · reconnecting`;
}
