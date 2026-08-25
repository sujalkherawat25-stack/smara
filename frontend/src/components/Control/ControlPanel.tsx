import { useCallback, useEffect, useRef, useState } from "react";
import { AlertCircle, ExternalLink, RefreshCw, ShieldCheck } from "lucide-react";

const CONTROL_ORIGIN = (import.meta.env.VITE_CONTROL_URL || "https://control-staging.syntarus.com").replace(/\/$/, "");
// New URL version deliberately bypasses a browser's cached pre-embed response
// which carried the old X-Frame-Options: DENY policy.
const CONTROL_APP_URL = `${CONTROL_ORIGIN}/app/?embed=smara-v3`;

type ControlToken = { token: string; expires_in_seconds: number };

/**
 * Hosts the independent Control service inside the signed-in Smara shell.
 * The iframe never receives a Smara session cookie or a signing secret: it is
 * given a short-lived, audience-restricted Control token held only in memory.
 */
export default function ControlPanel() {
  const frame = useRef<HTMLIFrameElement>(null);
  const [ready, setReady] = useState(false);
  const [status, setStatus] = useState<"loading" | "connected" | "error">("loading");
  const [error, setError] = useState("");

  const sendToken = useCallback(async () => {
    try {
      setStatus("loading");
      const response = await fetch("/v1/auth/control-token", {
        method: "POST",
        credentials: "include",
      });
      if (!response.ok) throw new Error(response.status === 401 ? "Your Smara session expired. Sign in again to open Control." : "Control is temporarily unavailable.");
      const data = await response.json() as ControlToken;
      frame.current?.contentWindow?.postMessage(
        { type: "smara-control-token", token: data.token },
        CONTROL_ORIGIN,
      );
      setError("");
      setStatus("connected");
    } catch (cause) {
      setStatus("error");
      setError(cause instanceof Error ? cause.message : "Could not connect Control.");
    }
  }, []);

  // The iframe can finish booting after its load event has already fired. A
  // ready message makes token delivery reliable instead of depending on a
  // single timing-sensitive postMessage call.
  useEffect(() => {
    const onFrameReady = (event: MessageEvent) => {
      if (event.origin !== CONTROL_ORIGIN || event.source !== frame.current?.contentWindow) return;
      if (event.data?.type === "smara-control-ready") void sendToken();
      if (event.data?.type === "smara-control-token-ack") setStatus("connected");
    };
    window.addEventListener("message", onFrameReady);
    return () => window.removeEventListener("message", onFrameReady);
  }, [sendToken]);

  useEffect(() => {
    if (!ready) return;
    void sendToken();
    const refresh = window.setInterval(() => void sendToken(), 45_000);
    return () => window.clearInterval(refresh);
  }, [ready, sendToken]);

  return (
    <div className="flex h-full min-h-0 flex-col" style={{ background: "var(--bg-base)" }}>
      <div className="flex items-center justify-between gap-3 px-4 py-2.5 shrink-0" style={{ borderBottom: "1px solid var(--border-dim)", background: "var(--bg-surface)" }}>
        <div className="flex min-w-0 items-center gap-2">
          {status === "error" ? <AlertCircle size={14} color="#ef4444" /> : <ShieldCheck size={14} style={{ color: "var(--accent2)" }} />}
          <span className="truncate text-[12px]" style={{ color: status === "error" ? "#ef4444" : "var(--text-muted)" }}>
            {status === "connected" ? "Control is connected to your Smara account" : status === "loading" ? "Connecting secure Control workspace…" : error}
          </span>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <button onClick={() => void sendToken()} title="Reconnect Control" className="p-2 rounded-md" style={{ color: "var(--text-muted)" }}><RefreshCw size={14} /></button>
          <a href={CONTROL_APP_URL} target="_blank" rel="noreferrer" title="Open Control in a new tab" className="p-2 rounded-md" style={{ color: "var(--text-muted)" }}><ExternalLink size={14} /></a>
        </div>
      </div>
      <div className="relative flex-1 min-h-0">
        <iframe
          ref={frame}
          src={CONTROL_APP_URL}
          title="Smara Control"
          onLoad={() => setReady(true)}
          className="absolute inset-0 h-full w-full border-0"
          allow="notifications"
        />
        {status === "error" && <div className="absolute inset-x-0 top-0 p-3 text-center text-[12px]" style={{ background: "rgba(239,68,68,.12)", color: "#ef4444" }}>{error}</div>}
      </div>
    </div>
  );
}
