import { useEffect, useState } from "react";
import ChatWindow from "@/components/Chat/ChatWindow";
import PdfPreviewPanel from "@/components/Chat/PdfPreviewPanel";
import RecentsPanel from "@/components/Chat/RecentsPanel";
import Notifications from "@/components/Notifications";
import SettingsPanel from "@/components/Settings/SettingsPanel";
import SmaraWorkPanel from "@/components/SmaraWorkPanel";
import SmaraLogo from "@/components/SmaraLogo";
import SignInScreen from "@/components/Auth/SignInScreen";
import MaintenanceScreen from "@/components/Auth/MaintenanceScreen";
import { useSSE } from "@/hooks/useSSE";
import { useThemeStore } from "@/stores/themeStore";
import { useViewStore } from "@/stores/viewStore";
import { useChatStore } from "@/stores/chatStore";
import { useAuthStore } from "@/stores/authStore";
import { smaraFetch, smaraModeEnabled } from "@/lib/smaraGateway";
import { Sun, Moon, ArrowLeft, Clock, Plus, Settings, LogOut, ListChecks } from "lucide-react";

/**
 * App — top-level auth gate.
 *
 * Three render branches:
 *   loading      → splash (the boot /me probe is in flight)
 *   unreachable  → <MaintenanceScreen /> (server mid-deploy — auto-retries)
 *   signed_out   → <SignInScreen />
 *   signed_in    → <AuthenticatedApp /> (the actual product)
 *
 * useSSE and other hooks that hit the backend are kept INSIDE
 * AuthenticatedApp so they don't fire before the user is authenticated.
 */
export default function App() {
  const status = useAuthStore((s) => s.status);
  const loadAccount = useAuthStore((s) => s.loadAccount);
  useEffect(() => {
    loadAccount();
  }, [loadAccount]);

  if (status === "loading") {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-950 text-gray-400">
        <div className="flex items-center gap-3">
          <SmaraLogo size={28} animate />
          <span className="text-sm">loading…</span>
        </div>
      </div>
    );
  }

  if (status === "unreachable") {
    return <MaintenanceScreen />;
  }

  if (status === "signed_out") {
    return <SignInScreen />;
  }

  return <AuthenticatedApp />;
}

function AuthenticatedApp() {
  const { connected } = useSSE();
  const { theme, toggle } = useThemeStore();
  const { view, setView, backToChat } = useViewStore();
  const newChat = useChatStore((s) => s.newChat);
  // Settings icon always opens the SettingsPanel. Operator-pays now — every
  // user gets the same Sarvam + Gemini routing; no per-user keys, no gates.
  const openSettings = () => setView("settings");

  // New chat + Projects now live inside this sidebar (not the top header),
  // so it defaults OPEN on desktop — otherwise those actions would be
  // hidden behind an extra click on first load. Mobile still starts closed
  // (it's a full-screen overlay drawer there, not a pushed column).
  const [sidebarOpen, setSidebarOpen] = useState(
    () => typeof window !== "undefined" && window.innerWidth >= 768,
  );

  /* Legacy channel status is intentionally not mounted in the focused shell. */
  /*

    // J3: Google's consent flow lands back here with ?google=connected /
    // declined / error. Surface the outcome as a toast and clean the URL.
    const params = new URLSearchParams(window.location.search);
    const google = params.get("google");
    if (google) {
      const push = useNotificationsStore.getState().push;
      if (google === "connected") {
        push({
          kind: "info",
          title: "Google connected",
          body: "Smara can now use Gmail and Calendar. Sends and event creation will still ask for your approval each time.",
          ttl_ms: 8000,
        });
      } else if (google === "declined") {
        push({
          kind: "info",
          title: "Google connection cancelled",
          body: "No access was granted. You can connect any time from Settings → Google.",
          ttl_ms: 6000,
        });
      } else {
        push({
          kind: "error",
          title: "Google connection failed",
          body: "Something went wrong during the consent flow — try again from Settings → Google.",
          ttl_ms: 8000,
        });
      }
      params.delete("google");
      const rest = params.toString();
      window.history.replaceState(
        null, "", window.location.pathname + (rest ? `?${rest}` : ""),
      );
    }
  */

  /*
  // Refresh Telegram link status whenever the tab regains focus or becomes
  // visible. This is the realistic post-/link flow: user generates code on
  // web → switches to Telegram → sends /link CODE → comes back to web tab
  // → focus/visibility event fires → status refreshes → chip turns green.
  // No interval polling needed (the link state changes rarely + only via
  // user action).
  useEffect(() => {
    const onFocus = () => { refreshTg(); };
    const onVisibility = () => {
      if (document.visibilityState === "visible") refreshTg();
    };
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [refreshTg]);
  */
  const isPanel = view === "work" || view === "settings";

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        if (isPanel) backToChat();
        else setSidebarOpen(false);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [isPanel, backToChat]);

  return (
    <div
      className="flex flex-col overflow-hidden"
      style={{
        // 100dvh = dynamic viewport height; on mobile this excludes the
        // browser chrome (URL bar, bottom toolbar) reliably, so the column
        // never overflows the visible area and the header can't scroll off.
        // h-screen / 100vh used to cause the header to disappear on
        // scroll-down on iOS Safari + Chrome Android.
        height: "100dvh",
        // Fallback for older browsers that don't know dvh.
        minHeight: "100vh",
        background: "var(--bg-base)",
        color: "var(--text-primary)",
        // Safe-area insets live HERE, inside the column, so the header sits
        // below the notch and the chat input above the home indicator.
        paddingTop: "env(safe-area-inset-top)",
        paddingBottom: "env(safe-area-inset-bottom)",
      }}
    >
      {/* Toast/notification stack (reminders, scheduled-task results) */}
      <Notifications />

      {/* A desktop/CLI login opens this same authenticated Smara shell with a
          short-lived cli_device query parameter. Keep approval in the shell
          so the user never has to type a token or visit the legacy Memento
          root. */}
      <CliDeviceApproval />

      {/* ── Header ─────────────────────────────────────────────────────── */}
      <header
        className="app-header flex items-center justify-between px-3 sm:px-5 shrink-0 z-30"
        style={{
          height: "50px",
          background: "var(--bg-base)",
          borderBottom: "1px solid var(--border-dim)",
          boxShadow: "0 1px 0 var(--border-dim)",
        }}
      >
        {/* ──────────────────────────────────────────────────────────────
            HEADER LAYOUT — completely different shape per device:

            Mobile (default, <md):
              Left:  ☰ hamburger (opens recents drawer)
              Right: ⚙ settings (everything — telegram, theme, sign-out — moves
                                 into the SettingsPanel's Account section)

            Desktop (md+):
              Left:  Clock · New chat · divider · Smara logo + tagline
              Right: live pill · telegram chip · settings · theme · sign-out
        ────────────────────────────────────────────────────────────── */}

        {/* Left — mobile shows only hamburger; desktop shows full toolbar.
            New chat + Projects live in the left sidebar now (Claude-style
            nav), not here — this stays just the sidebar toggle + logo. */}
        <div className="flex items-center gap-1.5 sm:gap-2 min-w-0">
          <IconButton
            active={sidebarOpen}
            onClick={() => setSidebarOpen((o) => !o)}
            title={sidebarOpen ? "Close sidebar" : "Open sidebar"}
          >
            <Clock size={15} />
          </IconButton>

          {/* Divider — desktop only */}
          <div className="hidden md:block h-4 w-px mx-1" style={{ background: "var(--border-default)" }} />

          {/* Logo — tagline hidden on mobile */}
          <div className="flex items-center gap-2 select-none min-w-0">
            <SmaraLogo size={28} animate />
            <div className="flex flex-col leading-none min-w-0">
              <span
                className="font-display text-[16px] font-extrabold truncate"
                style={{ color: "var(--text-primary)", letterSpacing: "-0.01em" }}
              >
                Smara
              </span>
              <span
                className="hidden md:inline text-[10px] font-medium tracking-normal truncate"
                style={{ color: "var(--accent)", lineHeight: 1.25 }}
              >
              </span>
            </div>
          </div>
        </div>

        {/* Right — desktop has the full toolbar, mobile shows ONLY settings */}
        <div className="flex items-center gap-1 sm:gap-2 shrink-0">
          {/* Desktop-only: live pill */}
          <div
            className="hidden md:flex items-center gap-1.5 px-2 py-1 rounded-full"
            style={{
              background: connected ? "var(--accent2-soft)" : "rgba(100,116,139,0.06)",
              border: `1px solid ${connected ? "rgba(52,211,153,0.20)" : "var(--border-dim)"}`,
            }}
            title={connected ? "Live" : "Offline"}
          >
            <div
              className="w-1.5 h-1.5 rounded-full"
              style={{
                background: connected ? "var(--accent2)" : "var(--text-muted)",
                boxShadow: connected ? "0 0 5px var(--accent2)" : "none",
              }}
            />
            <span
              className="text-[11px]"
              style={{ color: connected ? "var(--accent2)" : "var(--text-muted)" }}
            >
              {connected ? "live" : "offline"}
            </span>
          </div>

          {/* Settings — ALWAYS visible (mobile's single right-side action) */}
          <IconButton onClick={openSettings} title="Settings">
            <Settings size={14} />
          </IconButton>

          {/* Desktop-only: theme toggle (lives in Settings on mobile) */}
          <div className="hidden md:block">
            <IconButton onClick={toggle} title={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}>
              {theme === "dark" ? <Sun size={14} /> : <Moon size={14} />}
            </IconButton>
          </div>

          {/* Desktop-only: sign-out (lives in Settings on mobile) */}
          <div className="hidden md:block">
            <IconButton
              onClick={() => useAuthStore.getState().signOut()}
              title="Sign out"
            >
              <LogOut size={14} />
            </IconButton>
          </div>
        </div>
      </header>

      {/* ── Body ───────────────────────────────────────────────────────── */}
      <div className="flex flex-1 min-h-0 overflow-hidden relative">

        {/* Mobile sidebar backdrop (md hidden — desktop sidebar pushes content instead) */}
        {sidebarOpen && (
          <div
            onClick={() => setSidebarOpen(false)}
            className="md:hidden absolute inset-0 z-40 animate-fade-in"
            style={{ background: "rgba(0,0,0,0.45)", backdropFilter: "blur(2px)" }}
          />
        )}

        {/* Recents sidebar — pushes content on desktop, slides over on mobile */}
        <div
          className={`
            flex flex-col overflow-hidden transition-all duration-300 ease-in-out
            md:shrink-0 md:relative
            ${sidebarOpen ? "absolute md:relative inset-y-0 left-0 z-50" : ""}
          `}
          style={{
            width: sidebarOpen ? "min(85vw, 280px)" : "0px",
            borderRight: sidebarOpen ? "1px solid var(--border-dim)" : "none",
            background: "var(--bg-base)",
            boxShadow: sidebarOpen ? "var(--shadow-lg)" : "none",
          }}
        >
          {sidebarOpen && (
            <>
              {/* Header bar — mobile-only close (desktop uses the header toggle) */}
              <div
                className="flex items-center justify-between px-4 py-3 shrink-0 md:hidden"
              >
                <span className="text-[11px] font-semibold uppercase tracking-[0.18em]" style={{ color: "var(--text-dim)" }}>
                  Workspace
                </span>
                <button
                  onClick={() => setSidebarOpen(false)}
                  className="p-1.5 rounded-md min-h-[36px] min-w-[36px] flex items-center justify-center active:opacity-70"
                  style={{ color: "var(--text-muted)" }}
                  aria-label="Close menu"
                >
                  <ArrowLeft size={16} />
                </button>
              </div>

              {/* Nav — New chat, projects, and work */}
              <div className="flex flex-col gap-1 px-2.5 pt-3 pb-2 shrink-0">
                <SidebarNavRow
                  icon={<Plus size={15} />}
                  label="New chat"
                  shortcut="⌘ N"
                  onClick={() => { newChat(); setSidebarOpen(false); }}
                />
                <SidebarNavRow
                  icon={<ListChecks size={15} />}
                  label="Work"
                  onClick={() => { setView("work"); setSidebarOpen(false); }}
                />
              </div>

              <div
                className="px-4 pt-4 pb-2 shrink-0"
              >
                <span
                  className="text-[10px] font-semibold uppercase tracking-widest"
                  style={{ color: "var(--text-muted)" }}
                >
                  Recent chats
                </span>
              </div>

              {/* Recents list — fills the middle */}
              <div className="flex-1 min-h-0 overflow-hidden">
                <RecentsPanel onSelect={() => setSidebarOpen(false)} />
              </div>

              {/* Footer: avatar only (Claude-style) — New chat moved to the nav above */}
              <div
                className="flex items-center gap-2.5 px-3 py-3 shrink-0"
                style={{
                  borderTop: "1px solid var(--border-dim)",
                  background: "var(--bg-surface)",
                }}
              >
                <AccountAvatar onClick={() => { setSidebarOpen(false); setView("settings"); }} />
                <span className="text-[12px] truncate" style={{ color: "var(--text-muted)" }}>
                  Account &amp; settings
                </span>
              </div>
            </>
          )}
        </div>

        {/* Main */}
        <div className="flex-1 min-w-0 relative overflow-hidden">
          {/* Chat — always mounted, dimmed behind panel */}
          <div
            className="absolute inset-0 transition-all duration-300"
            style={{
              filter: isPanel ? "blur(2px)" : "none",
              opacity: isPanel ? 0.25 : 1,
              pointerEvents: isPanel ? "none" : "auto",
            }}
          >
            <ChatWindow />
          </div>

          {/* Click-outside backdrop */}
          {isPanel && (
            <div
              className="absolute inset-0 z-20"
              style={{ background: "var(--panel-backdrop)" }}
              onClick={backToChat}
            />
          )}

          {/* Settings panel — account, hosted runtime, and desktop */}
          {view === "settings" && (
            <PanelView
              title="Settings"
              icon={
                <div className="w-5 h-5 rounded-md flex items-center justify-center" style={{ background: "var(--accent-soft)" }}>
                  <Settings size={12} style={{ color: "var(--accent)" }} />
                </div>
              }
              onBack={backToChat}
            >
              <SettingsPanel onOpenControl={() => setView("work")} />
            </PanelView>
          )}

          {view === "work" && (
            <PanelView
              title="Work"
              icon={<div className="w-5 h-5 rounded-md flex items-center justify-center" style={{ background: "var(--accent2-soft)" }}><ListChecks size={12} style={{ color: "var(--accent2)" }} /></div>}
              onBack={backToChat}
            >
              <SmaraWorkPanel />
            </PanelView>
          )}

        </div>

        {/* PDF preview — persistent side panel, pushes the chat column aside
            (does NOT use the PanelView dim/overlay pattern above; the user
            should be able to keep chatting with the document open). */}
        <PdfPreviewPanel />
      </div>
    </div>
  );
}

function CliDeviceApproval() {
  const [deviceCode, setDeviceCode] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [finished, setFinished] = useState(false);

  useEffect(() => {
    if (!smaraModeEnabled()) return;
    const code = new URLSearchParams(window.location.search).get("cli_device");
    if (code) setDeviceCode(code);
  }, []);

  function close() {
    const params = new URLSearchParams(window.location.search);
    params.delete("cli_device");
    const suffix = params.toString();
    window.history.replaceState(null, "", `${window.location.pathname}${suffix ? `?${suffix}` : ""}${window.location.hash}`);
    setDeviceCode(null);
  }

  async function approve() {
    if (!deviceCode || busy) return;
    setBusy(true);
    setMessage(null);
    try {
      const response = await smaraFetch("/v1/cli/device/authorize", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ device_code: deviceCode }),
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({})) as { detail?: string };
        throw new Error(payload.detail || `Approval failed (${response.status})`);
      }
      setFinished(true);
      setMessage("Desktop approved. Return to Smara Desktop; it will finish signing in automatically.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Approval could not be completed. Try Sign in again.");
    } finally {
      setBusy(false);
    }
  }

  if (!deviceCode) return null;
  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center p-4" style={{ background: "rgba(2,6,23,.72)", backdropFilter: "blur(8px)" }}>
      <section className="w-full max-w-md rounded-2xl p-6 shadow-2xl" style={{ background: "var(--bg-card)", border: "1px solid var(--border-default)" }} role="dialog" aria-modal="true" aria-labelledby="cli-approval-title">
        <div className="flex items-start gap-3">
          <div className="w-10 h-10 rounded-xl flex items-center justify-center shrink-0" style={{ background: "var(--accent-soft)", color: "var(--accent)" }}>⌁</div>
          <div className="min-w-0 flex-1"><h2 id="cli-approval-title" className="text-lg font-semibold" style={{ color: "var(--text-primary)" }}>{finished ? "Desktop approved" : "Approve Smara Desktop"}</h2><p className="text-sm mt-1" style={{ color: "var(--text-muted)" }}>{finished ? "This window can be closed safely." : "A desktop is asking to connect to your Smara account. Approve only if you started this sign-in."}</p></div>
          <button type="button" onClick={close} className="p-1 text-lg" style={{ color: "var(--text-muted)" }} aria-label="Close">×</button>
        </div>
        <div className="mt-5 rounded-xl px-4 py-3" style={{ background: "var(--bg-surface)", border: "1px solid var(--border-dim)" }}><p className="text-[10px] uppercase tracking-[.16em]" style={{ color: "var(--text-muted)" }}>Request</p><p className="text-sm mt-1 font-medium" style={{ color: "var(--text-secondary)" }}>Smara Desktop · one-time browser approval</p></div>
        {message && <p className={`mt-4 text-sm ${finished ? "text-emerald-400" : "text-rose-400"}`} role="status">{message}</p>}
        <div className="flex justify-end gap-2 mt-6"><button type="button" onClick={close} className="px-4 py-2 rounded-lg text-sm" style={{ border: "1px solid var(--border-dim)", color: "var(--text-secondary)" }}>{finished ? "Close" : "Cancel"}</button>{!finished && <button type="button" onClick={() => void approve()} disabled={busy} className="px-4 py-2 rounded-lg text-sm font-semibold disabled:opacity-60" style={{ background: "var(--accent)", color: "var(--bg-base)" }}>{busy ? "Approving…" : "Approve desktop"}</button>}</div>
      </section>
    </div>
  );
}

/* ── Reusable icon button ─────────────────────────────────────────────────── */
/* ── Account avatar (sidebar footer) ────────────────────────────────────── */
function AccountAvatar({ onClick }: { onClick: () => void }) {
  const account = useAuthStore((s) => s.account);
  const name = (account?.display_name || account?.email || "U").trim();
  // Take up to two initials from the first + last word of the name
  const parts = name.split(/\s+/).filter(Boolean);
  const initials = (
    (parts[0]?.[0] || "") + (parts.length > 1 ? parts[parts.length - 1][0] : "")
  ).toUpperCase().slice(0, 2) || "U";

  return (
    <button
      onClick={onClick}
      className="w-9 h-9 rounded-full flex items-center justify-center text-[12px] font-semibold transition-all active:opacity-70 shrink-0"
      style={{
        background: "var(--accent-soft)",
        color: "var(--accent)",
        border: "1px solid var(--border-accent)",
      }}
      title="Account · Settings"
      aria-label="Open account settings"
    >
      {initials}
    </button>
  );
}


function IconButton({
  children, onClick, title, active,
}: {
  children: React.ReactNode;
  onClick: () => void;
  title?: string;
  active?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      title={title}
      className="w-9 h-9 sm:w-7 sm:h-7 flex items-center justify-center rounded-lg transition-all duration-150 active:opacity-70"
      aria-label={title}
      style={{
        background: active ? "var(--accent-soft)" : "transparent",
        border: active ? "1px solid var(--border-accent)" : "1px solid transparent",
        color: active ? "var(--accent)" : "var(--text-muted)",
      }}
      onMouseEnter={(e) => {
        if (!active) {
          (e.currentTarget as HTMLButtonElement).style.background = "var(--bg-surface)";
          (e.currentTarget as HTMLButtonElement).style.borderColor = "var(--border-dim)";
          (e.currentTarget as HTMLButtonElement).style.color = "var(--text-secondary)";
        }
      }}
      onMouseLeave={(e) => {
        if (!active) {
          (e.currentTarget as HTMLButtonElement).style.background = "transparent";
          (e.currentTarget as HTMLButtonElement).style.borderColor = "transparent";
          (e.currentTarget as HTMLButtonElement).style.color = "var(--text-muted)";
        }
      }}
    >
      {children}
    </button>
  );
}

/* ── Sidebar nav row (New chat / Projects) ───────────────────────────────── */
function SidebarNavRow({
  icon, label, shortcut, onClick, active,
}: {
  icon: React.ReactNode;
  label: string;
  shortcut?: string;
  onClick: () => void;
  active?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      className="flex items-center gap-2.5 px-2.5 py-2 rounded-lg text-[13px] font-medium transition-colors active:opacity-80"
      style={{
        background: active ? "var(--accent-soft)" : "transparent",
        color: active ? "var(--accent)" : "var(--text-secondary)",
      }}
      onMouseEnter={(e) => {
        if (!active) (e.currentTarget as HTMLButtonElement).style.background = "var(--bg-surface)";
      }}
      onMouseLeave={(e) => {
        if (!active) (e.currentTarget as HTMLButtonElement).style.background = "transparent";
      }}
    >
      <span className="grid place-items-center w-5 h-5 rounded-md" style={{ background: active ? "var(--accent-dim)" : "var(--bg-elevated)", color: active ? "var(--accent)" : "var(--text-muted)" }}>
        {icon}
      </span>
      <span className="flex-1 text-left">{label}</span>
      {shortcut && <span className="text-[10px] font-normal" style={{ color: "var(--text-dim)" }}>{shortcut}</span>}
    </button>
  );
}

/* ── Floating panel ───────────────────────────────────────────────────────── */
function PanelView({
  title, icon, onBack, narrow = false, children,
}: {
  title: string;
  icon: React.ReactNode;
  onBack: () => void;
  narrow?: boolean;
  children: React.ReactNode;
}) {
  return (
    // Mobile: full-bleed (no padding, no rounded corners, no max-width).
    // Desktop (md+): floats as a card with 20px gutter.
    <div
      className="absolute inset-0 z-30 flex items-stretch md:items-center md:justify-center pointer-events-none animate-panel-in p-0 md:p-5"
    >
      <div
        className="flex flex-col w-full h-full md:rounded-2xl overflow-hidden pointer-events-auto"
        style={{
          maxWidth: narrow ? "700px" : "1080px",
          background: "var(--bg-surface)",
          // No border on mobile (full-bleed); restore on desktop
          border: undefined,
          boxShadow: "var(--shadow-xl), 0 0 0 1px rgba(255,255,255,0.03)",
        }}
      >
        {/* Panel header — tap-friendly height on mobile, sleeker on desktop */}
        <div
          className="flex items-center justify-between px-4 md:px-5 py-2.5 md:py-3 shrink-0 min-h-[48px]"
          style={{
            borderBottom: "1px solid var(--border-dim)",
            background: "var(--bg-elevated)",
          }}
        >
          <div className="flex items-center gap-2.5 min-w-0">
            {icon}
            <span
              className="text-sm font-medium truncate"
              style={{ color: "var(--text-primary)" }}
            >
              {title}
            </span>
          </div>
          <button
            onClick={onBack}
            className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-[12px] transition-all duration-150 shrink-0 min-h-[36px]"
            style={{
              background: "var(--bg-card)",
              border: "1px solid var(--border-default)",
              color: "var(--text-muted)",
            }}
            onMouseEnter={(e) => {
              const b = e.currentTarget as HTMLButtonElement;
              b.style.borderColor = "var(--border-accent)";
              b.style.color = "var(--accent)";
            }}
            onMouseLeave={(e) => {
              const b = e.currentTarget as HTMLButtonElement;
              b.style.borderColor = "var(--border-default)";
              b.style.color = "var(--text-muted)";
            }}
            title="Back to chat (Esc)"
            aria-label="Close"
          >
            <ArrowLeft size={14} />
            <span className="hidden sm:inline">Close</span>
          </button>
        </div>

        <div className="flex-1 min-h-0 overflow-hidden">
          {children}
        </div>
      </div>
    </div>
  );
}
