/**
 * Settings/panels/GooglePanel.tsx — Gmail + Calendar OAuth grant.
 *
 * Two states:
 *   • Not connected → "Connect Google" CTA. On click, calls the backend to
 *     mint a state token, then navigates the window to Google's consent
 *     screen. After approval, Google bounces back to
 *     /v1/auth/google/callback → /?google=connected and the toast fires.
 *   • Connected → status card with granted features + date + Disconnect
 *     button (two-step confirm).
 *
 * Writes (send_email, create_calendar_event) are still gated by the J1
 * per-call approval prompt — connecting only authorises READ + write-with-
 * confirm, not silent sending.
 */

import { useState, useEffect } from "react";
import { CheckCircle, AlertCircle, ExternalLink, Mail, Calendar as CalIcon } from "lucide-react";
import { useGoogleStore } from "@/stores/googleStore";
import { startGoogleConnect } from "@/lib/google";

export default function GooglePanel() {
  const status = useGoogleStore((s) => s.status);
  const loaded = useGoogleStore((s) => s.loaded);
  const refresh = useGoogleStore((s) => s.refresh);
  const disconnect = useGoogleStore((s) => s.disconnect);

  const [connecting, setConnecting] = useState(false);
  const [confirmDisconnect, setConfirmDisconnect] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => { if (!loaded) refresh(); }, [loaded, refresh]);

  if (!loaded) {
    return <p className="text-[12px]" style={{ color: "var(--text-muted)" }}>Loading status…</p>;
  }

  // Backend says feature isn't configured on this deployment.
  if (status && !status.available) {
    return (
      <div className="flex flex-col gap-3 max-w-md">
        <div
          className="px-4 py-3 rounded-xl flex items-start gap-2.5"
          style={{ background: "var(--bg-card)", border: "1px solid var(--border-default)" }}
        >
          <AlertCircle size={14} style={{ color: "var(--text-muted)", marginTop: 2 }} />
          <div className="flex-1">
            <p className="text-[13px] font-medium" style={{ color: "var(--text-primary)" }}>
              Google integrations not enabled
            </p>
            <p className="text-[11px] mt-1" style={{ color: "var(--text-muted)" }}>
              This deployment is missing the Google OAuth client credentials
              (GOOGLE_CLIENT_SECRET). Contact the operator to enable.
            </p>
          </div>
        </div>
      </div>
    );
  }

  const isConnected = !!status?.connected;

  const handleConnect = async () => {
    setConnecting(true);
    setError(null);
    try {
      const { auth_url } = await startGoogleConnect(["gmail", "calendar"]);
      // Full-page navigate — Google requires top-level navigation for OAuth.
      window.location.href = auth_url;
    } catch (e) {
      setConnecting(false);
      setError(e instanceof Error ? e.message : "Couldn't start the Google flow.");
    }
  };

  const handleDisconnect = async () => {
    if (!confirmDisconnect) { setConfirmDisconnect(true); return; }
    setBusy(true);
    setError(null);
    try {
      const ok = await disconnect();
      if (!ok) setError("Couldn't disconnect. Try again.");
      setConfirmDisconnect(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't disconnect.");
    } finally {
      setBusy(false);
    }
  };

  // ── Connected ───────────────────────────────────────────────────────────
  if (isConnected && status) {
    const hasGmail = status.features.includes("gmail");
    const hasCal = status.features.includes("calendar");
    return (
      <div className="flex flex-col gap-4 max-w-md">
        <div
          className="px-4 py-3.5 rounded-xl flex items-center gap-2.5"
          style={{ background: "var(--bg-card)", border: "1px solid var(--border-default)" }}
        >
          <div
            className="w-8 h-8 rounded-lg flex items-center justify-center shrink-0"
            style={{ background: "rgba(34,197,94,0.12)" }}
          >
            <CheckCircle size={15} style={{ color: "var(--accent2)" }} />
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-[13px] font-medium" style={{ color: "var(--text-primary)" }}>
              Google connected
            </p>
            <p className="text-[11px] mt-0.5" style={{ color: "var(--text-muted)" }}>
              {status.granted_at
                ? `Granted ${new Date(status.granted_at).toLocaleDateString(undefined, { dateStyle: "medium" })}`
                : "Granted"}
            </p>
          </div>
        </div>

        {/* Per-feature granted badges */}
        <div className="flex flex-col gap-2">
          <FeatureRow
            icon={<Mail size={13} />}
            label="Gmail"
            description="Read inbox · send with per-message approval"
            granted={hasGmail}
          />
          <FeatureRow
            icon={<CalIcon size={13} />}
            label="Google Calendar"
            description="List events · create events with per-call approval"
            granted={hasCal}
          />
        </div>

        {!(hasGmail && hasCal) && (
          <p className="text-[11px] leading-relaxed" style={{ color: "var(--text-muted)" }}>
            Some features weren't approved on Google's screen. Reconnect to
            re-grant — Smara won't try to use features it doesn't have access to.
          </p>
        )}

        <p className="text-[12px] leading-relaxed" style={{ color: "var(--text-secondary)" }}>
          Every <span className="font-medium" style={{ color: "var(--text-primary)" }}>write</span> action
          (sending email, creating events) still asks for your approval at the moment of action.
          Connecting only grants access — not autonomy.
        </p>

        {error && <p className="text-[11px]" style={{ color: "#ef4444" }}>{error}</p>}

        <div className="flex items-center gap-2">
          <button
            onClick={handleConnect}
            disabled={connecting}
            className="flex-1 py-2 rounded-xl text-[13px] font-medium transition-all"
            style={{
              background: "var(--bg-card)",
              color: "var(--text-secondary)",
              border: "1px solid var(--border-default)",
            }}
            title="Re-run the consent flow (e.g. to add a feature you skipped)"
          >
            {connecting ? "Redirecting…" : "Re-consent"}
          </button>
          <button
            onClick={handleDisconnect}
            disabled={busy}
            className="px-4 py-2 rounded-xl text-[13px] font-medium transition-all"
            style={{
              background: confirmDisconnect ? "rgba(239,68,68,0.10)" : "var(--bg-card)",
              color: confirmDisconnect ? "#ef4444" : "var(--text-secondary)",
              border: `1px solid ${confirmDisconnect ? "rgba(239,68,68,0.30)" : "var(--border-default)"}`,
            }}
          >
            {busy ? "Disconnecting…" : confirmDisconnect ? "Click again to confirm" : "Disconnect"}
          </button>
        </div>
      </div>
    );
  }

  // ── Not connected ───────────────────────────────────────────────────────
  return (
    <div className="flex flex-col gap-4 max-w-md">
      <p className="text-[13px] leading-relaxed" style={{ color: "var(--text-secondary)" }}>
        Let Smara read your inbox and calendar, draft emails, and create events.
        Read access is silent; sending or creating anything always asks for your
        approval at the moment it would happen.
      </p>

      <div className="flex flex-col gap-2">
        <FeatureRow
          icon={<Mail size={13} />}
          label="Gmail"
          description="Read inbox · send with per-message approval"
        />
        <FeatureRow
          icon={<CalIcon size={13} />}
          label="Google Calendar"
          description="List events · create events with per-call approval"
        />
      </div>

      {error && <p className="text-[11px]" style={{ color: "#ef4444" }}>{error}</p>}

      <button
        onClick={handleConnect}
        disabled={connecting}
        className="flex items-center justify-center gap-2 py-2.5 rounded-xl text-[13px] font-medium transition-all"
        style={{ background: "var(--accent)", color: "#fff" }}
      >
        <ExternalLink size={13} />
        {connecting ? "Redirecting to Google…" : "Connect Google"}
      </button>

      <p className="text-[11px] leading-relaxed" style={{ color: "var(--text-muted)" }}>
        You'll be redirected to Google's consent screen. Untick any feature
        you don't want Smara to access — we'll respect what you grant.
      </p>
    </div>
  );
}


function FeatureRow({
  icon, label, description, granted,
}: {
  icon: React.ReactNode;
  label: string;
  description: string;
  granted?: boolean;
}) {
  return (
    <div
      className="px-3 py-2 rounded-lg flex items-center gap-2.5"
      style={{
        background: "var(--bg-card)",
        border: "1px solid var(--border-dim)",
        opacity: granted === false ? 0.55 : 1,
      }}
    >
      <div
        className="w-7 h-7 rounded-md flex items-center justify-center shrink-0"
        style={{ background: "var(--accent-soft)", color: "var(--accent)" }}
      >
        {icon}
      </div>
      <div className="flex-1 min-w-0">
        <p className="text-[12.5px] font-medium" style={{ color: "var(--text-primary)" }}>
          {label}
        </p>
        <p className="text-[11px]" style={{ color: "var(--text-muted)" }}>
          {description}
        </p>
      </div>
      {granted !== undefined && (
        <span
          className="text-[9px] px-1.5 py-0.5 rounded-full uppercase tracking-wider"
          style={{
            background: granted ? "rgba(52,211,153,0.10)" : "rgba(239,68,68,0.10)",
            color: granted ? "var(--accent2)" : "#ef4444",
          }}
        >
          {granted ? "granted" : "denied"}
        </span>
      )}
    </div>
  );
}
