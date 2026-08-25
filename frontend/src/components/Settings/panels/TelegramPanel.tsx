/**
 * Settings/panels/TelegramPanel.tsx — Telegram link flow.
 *
 * Two states:
 *   • Not linked → "Generate link code" CTA. On generate, shows the 6-digit
 *     code with countdown + a deep-link button to the bot. While the code is
 *     active we poll status every 3s so the UI flips to "Connected" the
 *     moment the user finishes /link CODE in Telegram.
 *   • Linked → "Telegram connected" card with linked date + last-4 of the
 *     Telegram user id + two-step Disconnect button.
 *
 * Status comes from the shared telegramStore so the header chip and the
 * panel always agree.
 */

import { useState, useEffect } from "react";
import { CheckCircle, Send, Copy } from "lucide-react";
import { generateTelegramLinkCode, type LinkCode } from "@/lib/auth";
import { useTelegramStore } from "@/stores/telegramStore";

export default function TelegramPanel() {
  const status = useTelegramStore((s) => s.status);
  const statusLoaded = useTelegramStore((s) => s.loaded);
  const refreshStatus = useTelegramStore((s) => s.refresh);
  const unlinkFromStore = useTelegramStore((s) => s.unlink);

  const [linkCode, setLinkCode] = useState<LinkCode | null>(null);
  const [requesting, setRequesting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [secondsLeft, setSecondsLeft] = useState(0);
  const [copied, setCopied] = useState<"code" | "url" | null>(null);
  const [confirmUnlink, setConfirmUnlink] = useState(false);
  const [unlinking, setUnlinking] = useState(false);

  const isLinked = !!status?.linked;

  // While a code is active and not yet linked, poll status — flips to
  // Connected as soon as /link CODE succeeds in Telegram.
  useEffect(() => {
    if (!linkCode || isLinked) return;
    const t = setInterval(() => { refreshStatus(); }, 3000);
    return () => clearInterval(t);
  }, [linkCode, isLinked, refreshStatus]);

  useEffect(() => {
    if (isLinked && linkCode) { setLinkCode(null); setSecondsLeft(0); }
  }, [isLinked, linkCode]);

  // Countdown so the displayed code drops when it would no longer redeem.
  useEffect(() => {
    if (!linkCode || secondsLeft <= 0) return;
    const t = setInterval(() => {
      setSecondsLeft((s) => {
        if (s <= 1) { setLinkCode(null); return 0; }
        return s - 1;
      });
    }, 1000);
    return () => clearInterval(t);
  }, [linkCode, secondsLeft]);

  // "Copied!" pill clears after 1.5s.
  useEffect(() => {
    if (!copied) return;
    const t = setTimeout(() => setCopied(null), 1500);
    return () => clearTimeout(t);
  }, [copied]);

  const handleGenerate = async () => {
    setRequesting(true);
    setError(null);
    try {
      const code = await generateTelegramLinkCode();
      setLinkCode(code);
      setSecondsLeft(code.expires_in_seconds);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't generate a code.");
    } finally {
      setRequesting(false);
    }
  };

  const copyToClipboard = async (text: string, label: "code" | "url") => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(label);
    } catch { /* clipboard blocked */ }
  };

  const handleUnlink = async () => {
    if (!confirmUnlink) { setConfirmUnlink(true); return; }
    setUnlinking(true);
    setError(null);
    try {
      await unlinkFromStore();
      setConfirmUnlink(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't disconnect Telegram.");
    } finally {
      setUnlinking(false);
    }
  };

  const mins = Math.floor(secondsLeft / 60);
  const secs = secondsLeft % 60;

  // ── Linked: show connected card ─────────────────────────────────────────
  if (isLinked && status) {
    return (
      <div className="flex flex-col gap-4 max-w-md">
        <div
          className="px-4 py-3.5 rounded-xl flex items-center justify-between"
          style={{ background: "var(--bg-card)", border: "1px solid var(--border-default)" }}
        >
          <div className="flex items-center gap-2.5">
            <div
              className="w-8 h-8 rounded-lg flex items-center justify-center"
              style={{ background: "rgba(34,197,94,0.12)" }}
            >
              <CheckCircle size={15} style={{ color: "var(--accent2)" }} />
            </div>
            <div>
              <p className="text-[13px] font-medium" style={{ color: "var(--text-primary)" }}>
                Telegram connected
              </p>
              <p className="text-[11px] mt-0.5" style={{ color: "var(--text-muted)" }}>
                {status.linked_at
                  ? `Linked ${new Date(status.linked_at).toLocaleDateString(undefined, { dateStyle: "medium" })}`
                  : "Linked"}
                {status.channel_user_preview ? ` · tg ${status.channel_user_preview}` : ""}
              </p>
            </div>
          </div>
        </div>

        <p className="text-[12px] leading-relaxed" style={{ color: "var(--text-secondary)" }}>
          Your Telegram chat and the web app share the same memory. Message the bot anytime — replies use the same agent loop.
        </p>

        {error && <p className="text-[11px]" style={{ color: "#ef4444" }}>{error}</p>}

        <button
          onClick={handleUnlink}
          disabled={unlinking}
          className="py-2 rounded-xl text-[13px] font-medium transition-all"
          style={{
            background: confirmUnlink ? "rgba(239,68,68,0.10)" : "var(--bg-card)",
            color: confirmUnlink ? "#ef4444" : "var(--text-secondary)",
            border: `1px solid ${confirmUnlink ? "rgba(239,68,68,0.30)" : "var(--border-default)"}`,
          }}
        >
          {unlinking ? "Disconnecting…" : confirmUnlink ? "Click again to confirm" : "Disconnect Telegram"}
        </button>
      </div>
    );
  }

  // ── Not linked: generate-code flow ──────────────────────────────────────
  return (
    <div className="flex flex-col gap-4 max-w-md">
      {!statusLoaded && (
        <p className="text-[12px]" style={{ color: "var(--text-muted)" }}>Checking link status…</p>
      )}
      {!linkCode && (
        <p className="text-[13px] leading-relaxed" style={{ color: "var(--text-secondary)" }}>
          Chat with Smara on Telegram with the same memory as the web app.
          Click below to generate a one-time 6-digit code, then send it as
          <span className="font-mono mx-1" style={{ color: "var(--text-primary)" }}>/link CODE</span>
          inside the bot.
        </p>
      )}

      {linkCode && (
        <>
          <div
            className="px-4 py-4 rounded-xl flex flex-col items-center gap-2"
            style={{ background: "var(--bg-card)", border: "1px solid var(--border-default)" }}
          >
            <span className="text-[10px] font-semibold uppercase tracking-widest" style={{ color: "var(--text-muted)" }}>
              Your link code
            </span>
            <button
              onClick={() => copyToClipboard(linkCode.code, "code")}
              className="text-[28px] font-mono tracking-[0.3em] py-1 px-2 rounded-md transition-all"
              style={{ color: "var(--text-primary)" }}
              title="Click to copy"
            >
              {linkCode.code}
            </button>
            <div className="flex items-center gap-2 text-[11px]" style={{ color: "var(--text-muted)" }}>
              <span>Expires in {mins}:{secs.toString().padStart(2, "0")}</span>
              {copied === "code" && <span className="ml-1" style={{ color: "var(--accent2)" }}>copied ✓</span>}
            </div>
          </div>

          <div className="flex items-center gap-2">
            <a
              href={linkCode.bot_url}
              target="_blank" rel="noopener noreferrer"
              className="flex-1 flex items-center justify-center gap-2 py-2.5 rounded-xl text-[13px] font-medium transition-all"
              style={{ background: "var(--accent)", color: "#fff" }}
            >
              <Send size={13} />
              Open bot in Telegram
            </a>
            <button
              onClick={() => copyToClipboard(linkCode.bot_url, "url")}
              className="px-3 py-2.5 rounded-xl transition-all"
              style={{
                background: "var(--bg-card)",
                border: "1px solid var(--border-default)",
                color: copied === "url" ? "var(--accent2)" : "var(--text-muted)",
              }}
              title="Copy bot URL"
            >
              <Copy size={13} />
            </button>
          </div>

          <div className="text-[12px] leading-relaxed" style={{ color: "var(--text-secondary)" }}>
            <p className="font-medium mb-1" style={{ color: "var(--text-primary)" }}>Next steps:</p>
            <ol className="space-y-1 pl-4 list-decimal">
              <li>Open the bot.</li>
              <li>Send:&nbsp;
                <span className="font-mono" style={{ color: "var(--text-primary)" }}>/link {linkCode.code}</span>
              </li>
              <li>Done — both channels share your memory.</li>
            </ol>
          </div>
        </>
      )}

      {error && <p className="text-[11px]" style={{ color: "#ef4444" }}>{error}</p>}

      <button
        onClick={handleGenerate}
        disabled={requesting}
        className="py-2 rounded-xl text-[13px] font-medium transition-all"
        style={{
          background: requesting ? "var(--bg-card)" : (linkCode ? "var(--bg-card)" : "var(--accent)"),
          color: requesting || linkCode ? "var(--text-secondary)" : "#fff",
          border: `1px solid ${linkCode ? "var(--border-default)" : "transparent"}`,
        }}
      >
        {requesting ? "Generating…" : linkCode ? "Generate new code" : "Generate link code"}
      </button>
    </div>
  );
}
