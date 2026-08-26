import { useEffect, useRef, useState } from "react";
import { useAuthStore } from "@/stores/authStore";
import SmaraLogo from "@/components/SmaraLogo";
import { Mail } from "lucide-react";

declare global {
  interface Window {
    google?: { accounts: { id: {
      initialize: (config: { client_id: string; callback: (resp: { credential: string }) => void; auto_select?: boolean; cancel_on_tap_outside?: boolean }) => void;
      renderButton: (element: HTMLElement, options: { type?: "standard" | "icon"; theme?: "outline" | "filled_blue" | "filled_black"; size?: "small" | "medium" | "large"; text?: "signin_with" | "signup_with" | "continue_with" | "signin"; shape?: "rectangular" | "pill" | "circle" | "square"; logo_alignment?: "left" | "center"; width?: string | number }) => void;
      prompt: () => void;
    } } };
  }
}

const CLIENT_ID = import.meta.env.VITE_GOOGLE_CLIENT_ID as string | undefined;

export default function SignInScreen() {
  const buttonRef = useRef<HTMLDivElement>(null);
  const signInWithGoogleToken = useAuthStore((s) => s.signInWithGoogleToken);
  const requestEmailOtp = useAuthStore((s) => s.requestEmailOtp);
  const verifyEmailOtp = useAuthStore((s) => s.verifyEmailOtp);
  const error = useAuthStore((s) => s.error);
  const [misconfigured, setMisconfigured] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [codeSent, setCodeSent] = useState(false);

  useEffect(() => {
    if (!CLIENT_ID) { setMisconfigured(true); return; }
    let cancelled = false;
    let attempts = 0;
    function init() {
      if (cancelled) return;
      if (!window.google?.accounts?.id) { attempts += 1; if (attempts <= 100) setTimeout(init, 100); return; }
      window.google.accounts.id.initialize({
        client_id: CLIENT_ID!,
        callback: async (resp) => { setSubmitting(true); try { await signInWithGoogleToken(resp.credential); } catch { /* store displays the error */ } finally { setSubmitting(false); } },
        auto_select: false,
        cancel_on_tap_outside: true,
      });
      if (buttonRef.current) window.google.accounts.id.renderButton(buttonRef.current, { type: "standard", theme: "filled_black", size: "large", text: "continue_with", shape: "pill", logo_alignment: "left" });
    }
    init();
    return () => { cancelled = true; };
  }, [signInWithGoogleToken]);

  const sendCode = async () => { setSubmitting(true); try { await requestEmailOtp(email); setCodeSent(true); } finally { setSubmitting(false); } };
  const verifyCode = async () => { setSubmitting(true); try { await verifyEmailOtp(email, code); } finally { setSubmitting(false); } };

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100 flex flex-col items-center justify-center px-6">
      <div className="flex flex-col items-center gap-6 max-w-md text-center">
        <SmaraLogo size={64} animate />
        <div className="space-y-2"><h1 className="text-3xl font-bold font-display">Welcome to Smara</h1><p className="text-gray-400 text-sm">Sign in to keep your conversations, memories, and ideas connected across devices.</p></div>
        {!misconfigured && <div ref={buttonRef} className={submitting ? "opacity-50 pointer-events-none" : ""} />}
        <div className="w-full max-w-sm border-t border-gray-800 pt-5 mt-2">
          <p className="text-gray-400 text-sm mb-3">Or use email</p>
          <div className="flex gap-2">
            <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@example.com" autoComplete="email" disabled={codeSent || submitting} className="flex-1 rounded-lg bg-gray-900 border border-gray-700 px-3 py-2 text-sm outline-none focus:border-emerald-400" />
            {!codeSent && <button type="button" disabled={!email.trim() || submitting} onClick={sendCode} className="rounded-lg bg-emerald-400 text-gray-950 px-3 py-2 text-sm font-semibold disabled:opacity-40">Send code</button>}
          </div>
          {codeSent && <><div className="mt-3 flex gap-2"><input inputMode="numeric" maxLength={6} value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))} placeholder="6-digit code" autoComplete="one-time-code" className="flex-1 rounded-lg bg-gray-900 border border-gray-700 px-3 py-2 text-sm tracking-widest outline-none focus:border-emerald-400" /><button type="button" disabled={code.length !== 6 || submitting} onClick={verifyCode} className="rounded-lg bg-white text-gray-950 px-3 py-2 text-sm font-semibold disabled:opacity-40">Verify</button></div><button type="button" onClick={() => { setCodeSent(false); setCode(""); }} className="text-xs text-gray-500 mt-2 hover:text-gray-300">Use a different email</button></>}
          <div className="flex items-center justify-center gap-2 text-gray-500 text-xs mt-3"><Mail size={13} /> We’ll email a one-time code. No password stored.</div>
        </div>
        {submitting && <p className="text-gray-500 text-xs">Signing you in…</p>}
        {error && !submitting && <p className="text-red-400 text-xs mt-2">{error}</p>}
        <p className="text-gray-600 text-xs mt-6">Google and email sign-in use the same secure Syntarus account.</p>
      </div>
    </div>
  );
}
