/**
 * Settings/SettingsPanel.tsx — full-page two-pane settings UI.
 *
 * Layout:
 *   ┌─────────────┬──────────────────────────────┐
 *   │ left rail   │  selected section content    │
 *   │ (sections)  │                              │
 *   └─────────────┴──────────────────────────────┘
 *
 * Sections are organised into GROUPS:
 *   • Connections   — channels Smara talks on (telegram; future whatsapp)
 *   • Integrations  — Google (Gmail + Calendar) — Founder-only for now
 *   • Automation    — scheduled tasks and reminders
 *   • Account       — profile, plan badge, usage meter, feedback
 *
 * LLM-connector / BYOK panel was removed when the stack moved to operator-pays
 * (Sarvam + Gemini via Vertex). Adding a new section = one entry in SECTIONS[]
 * below + a matching component.
 *
 * Loaded once when view becomes "settings" — refreshes each domain's data
 * (telegram link status, reminders, tasks) on entry so the user sees
 * current state.
 */

import { useEffect, useState } from "react";
import {
  Send, Bell, Calendar, Mail, User, Lock, Gauge,
  ChevronRight, ChevronLeft, Sun, Moon, LogOut, ListChecks,
} from "lucide-react";

import TelegramPanel from "./panels/TelegramPanel";
import RemindersPanel from "./panels/RemindersPanel";
import TasksPanel from "./panels/TasksPanel";
import GooglePanel from "./panels/GooglePanel";

import { CONNECTIONS } from "./connections";

import { useTelegramStore } from "@/stores/telegramStore";
import { useRemindersStore } from "@/stores/remindersStore";
import { useTasksStore } from "@/stores/tasksStore";
import { useGoogleStore } from "@/stores/googleStore";
import { useAuthStore } from "@/stores/authStore";
import { useThemeStore } from "@/stores/themeStore";
import { apiClient, ApiError } from "@/api/client";
import { smaraModeEnabled } from "@/lib/smaraGateway";
import SmaraFocusedSettings from "./SmaraFocusedSettings";

// ── Section identity ────────────────────────────────────────────────────────
// `id` follows "group:item" so the URL/route could be added later without a
// new schema. Group prefix also makes left-rail rendering trivial.

type SectionId =
  | `connection:${string}`
  | `integration:${string}`
  | "automation:reminders"
  | "automation:tasks"
  | "workspace:control"
  | "account:profile"
  | "account:usage";

// Icon typed as `any`-compatible because lucide-react exports
// ForwardRefExoticComponent<LucideProps>, which doesn't satisfy a strict
// ComponentType<{ size?: number }> under TS strict mode. We render with a
// fixed `size={13}` so the loose typing has no runtime impact.
type IconComponent = React.ComponentType<{ size?: number }>;

interface SectionGroup {
  id: string;
  label: string;
  items: { id: SectionId; label: string; icon: IconComponent; comingSoon?: boolean; locked?: boolean; }[];
}

export default function SettingsPanel({ onOpenControl }: { onOpenControl?: () => void }) {
  if (smaraModeEnabled()) return <SmaraFocusedSettings onOpenWork={onOpenControl} />;
  return <LegacySettingsPanel onOpenControl={onOpenControl} />;
}

function LegacySettingsPanel({ onOpenControl }: { onOpenControl?: () => void }) {
  // ── Domain refresh on mount ──────────────────────────────────────────
  const refreshTelegram = useTelegramStore((s) => s.refresh);
  const refreshReminders = useRemindersStore((s) => s.refresh);
  const refreshTasks = useTasksStore((s) => s.refresh);
  const refreshGoogle = useGoogleStore((s) => s.refresh);
  const account = useAuthStore((s) => s.account);
  const isFounder = (account?.plan || "free").toLowerCase() === "founder";

  useEffect(() => {
    refreshTelegram();
    refreshReminders();
    refreshTasks();
    refreshGoogle();
  }, [refreshTelegram, refreshReminders, refreshTasks, refreshGoogle]);

  // ── Build the section list dynamically from the registries ───────────
  const groups: SectionGroup[] = [
    {
      id: "connections",
      label: "Connections",
      items: CONNECTIONS.map((c) => ({
        id: `connection:${c.id}` as SectionId,
        label: c.label,
        icon: Send as unknown as IconComponent,
        comingSoon: c.comingSoon,
      })),
    },
    {
      id: "integrations",
      label: "Integrations",
      items: [
        // Gmail + Calendar are a single Google grant on the backend — surfaced
        // as one row. Founder-only until paid Pro tier lands; free users see
        // a padlock + "available on Pro" tooltip.
        {
          id: "integration:google",
          label: "Gmail & Calendar",
          icon: Mail as unknown as IconComponent,
          locked: !isFounder,
        },
      ],
    },
    {
      id: "automation",
      label: "Automation",
      items: [
        { id: "automation:reminders", label: "Reminders", icon: Bell as unknown as IconComponent },
        { id: "automation:tasks", label: "Tasks", icon: Calendar as unknown as IconComponent },
      ],
    },
    {
      id: "workspace",
      label: "Workspace",
      items: [
        { id: "workspace:control", label: "Control panel", icon: ListChecks as unknown as IconComponent },
      ],
    },
    {
      id: "account",
      label: "Account",
      items: [
        { id: "account:profile", label: "Profile & preferences", icon: User as unknown as IconComponent },
        { id: "account:usage", label: "Usage & limits", icon: Gauge as unknown as IconComponent },
      ],
    },
  ];

  // Pick the first non-blocked item as default selection.
  const firstSelectable = (() => {
    for (const g of groups) for (const item of g.items) if (!item.comingSoon && !item.locked) return item.id;
    return "connection:telegram" as SectionId;
  })();

  const [selected, setSelected] = useState<SectionId>(firstSelectable);

  // Mobile drawer state — `null` shows the section list, anything else shows
  // the detail view (with a back arrow). On md+ screens both panes are
  // visible at once and this state has no visible effect.
  const [mobileView, setMobileView] = useState<"list" | "detail">("list");

  // Renders the section list (used both as the left rail on desktop AND as
  // the first-level "menu" on mobile)
  const sectionList = (
    <>
      {groups.map((group, gi) => (
        <div key={group.id} className={gi > 0 ? "mt-4" : "mt-3"}>
          <p
            className="px-4 mb-1.5 text-[10px] font-semibold uppercase tracking-widest"
            style={{ color: "var(--text-muted)" }}
          >
            {group.label}
          </p>
          <div className="flex flex-col">
            {group.items.map((item) => {
              const active = selected === item.id;
              const Icon = item.icon;
              const blocked = item.comingSoon || item.locked;
              return (
                <button
                  key={item.id}
                  onClick={() => {
                    if (blocked) return;
                    setSelected(item.id);
                    setMobileView("detail");
                  }}
                  disabled={blocked}
                  title={
                    item.locked
                      ? "Available on Pro — coming soon. Founders have access today."
                      : item.comingSoon
                        ? "Coming soon."
                        : undefined
                  }
                  className="flex items-center gap-2.5 px-4 py-3 md:py-2 text-[14px] md:text-[13px] text-left transition-all min-h-[44px] md:min-h-0"
                  style={{
                    background: active ? "var(--accent-soft)" : "transparent",
                    color: active
                      ? "var(--accent)"
                      : blocked
                        ? "var(--text-dim)"
                        : "var(--text-secondary)",
                    borderLeft: active
                      ? "2px solid var(--accent)"
                      : "2px solid transparent",
                    cursor: blocked ? "not-allowed" : "pointer",
                  }}
                >
                  <Icon size={14} />
                  <span className="flex-1">{item.label}</span>
                  {item.locked && (
                    <span
                      className="flex items-center gap-1 text-[9px] px-1.5 py-0.5 rounded-full"
                      style={{
                        background: "var(--bg-card)",
                        color: "var(--text-muted)",
                        border: "1px solid var(--border-dim)",
                      }}
                    >
                      <Lock size={9} /> Pro
                    </span>
                  )}
                  {item.comingSoon && !item.locked && (
                    <span
                      className="text-[9px] px-1.5 py-0.5 rounded-full"
                      style={{
                        background: "var(--bg-card)",
                        color: "var(--text-muted)",
                        border: "1px solid var(--border-dim)",
                      }}
                    >
                      soon
                    </span>
                  )}
                  <ChevronRight
                    size={14}
                    style={{
                      color: active ? "var(--accent)" : "var(--text-muted)",
                      opacity: active || !blocked ? 0.7 : 0.3,
                    }}
                  />
                </button>
              );
            })}
          </div>
        </div>
      ))}
    </>
  );

  return (
    <div className="flex h-full" style={{ background: "var(--bg-base)" }}>
      {/* ── Left rail — visible always on md+, hidden when in detail-view on mobile ─ */}
      <div
        className={`
          ${mobileView === "list" ? "flex" : "hidden"} md:flex
          shrink-0 flex-col overflow-y-auto scrollbar-thin
          w-full md:w-[240px]
        `}
        style={{
          borderRight: "1px solid var(--border-dim)",
          background: "var(--bg-surface)",
        }}
      >
        {sectionList}
      </div>

      {/* ── Right pane — visible always on md+, only in detail-view on mobile ─ */}
      <div
        className={`
          ${mobileView === "detail" ? "flex" : "hidden"} md:flex
          flex-1 min-w-0 flex-col overflow-hidden
        `}
      >
        {/* Mobile-only "back to sections" bar */}
        <button
          onClick={() => setMobileView("list")}
          className="md:hidden flex items-center gap-1.5 px-4 py-2.5 text-[13px] shrink-0 min-h-[44px] active:opacity-70"
          style={{
            color: "var(--accent)",
            borderBottom: "1px solid var(--border-dim)",
            background: "var(--bg-surface)",
          }}
        >
          <ChevronLeft size={16} />
          Settings
        </button>

        {/* Scrollable section content */}
        <div className="flex-1 min-h-0 overflow-y-auto scrollbar-thin">
          <div className="w-full max-w-6xl mx-auto px-5 md:px-8 py-5 md:py-7">
            <SectionHeader selected={selected} />
            <div className="mt-5">
              <SectionContent selected={selected} onOpenControl={onOpenControl} />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}


// ── Per-section header (title + tagline) ────────────────────────────────────

function SectionHeader({ selected }: { selected: SectionId }) {
  let title = "";
  let tagline = "";

  if (selected.startsWith("connection:")) {
    const id = selected.slice("connection:".length);
    const c = CONNECTIONS.find((x) => x.id === id);
    if (c) { title = c.label; tagline = c.tagline; }
  } else if (selected === "integration:google") {
    title = "Gmail & Calendar";
    tagline = "Let Smara read your inbox + calendar; writes always ask for approval";
  } else if (selected === "automation:reminders") {
    title = "Reminders";
    tagline = "One-off fires Smara delivers verbatim at the chosen time";
  } else if (selected === "automation:tasks") {
    title = "Scheduled tasks";
    tagline = "Recurring or one-off agent runs that produce fresh output each fire";
  } else if (selected === "workspace:control") {
    title = "Control panel";
    tagline = "View long-running work, approvals, research evidence, integrations, and desktop execution";
  } else if (selected === "account:profile") {
    title = "Profile & preferences";
    tagline = "Your account, theme, and sign-out";
  } else if (selected === "account:usage") {
    title = "Usage & limits";
    tagline = "What you've used today and where your plan caps sit";
  }

  return (
    <div>
      <h1 className="text-[22px] font-display font-bold" style={{ color: "var(--text-primary)" }}>
        {title}
      </h1>
      <p className="mt-1 text-[13px]" style={{ color: "var(--text-muted)" }}>
        {tagline}
      </p>
    </div>
  );
}


// ── Per-section content router ──────────────────────────────────────────────

function SectionContent({ selected, onOpenControl }: { selected: SectionId; onOpenControl?: () => void }) {
  // Connections
  if (selected === "connection:telegram") return <TelegramPanel />;
  if (selected.startsWith("connection:")) {
    // Future channel — fall back to a coming-soon stub.
    return <ComingSoonStub />;
  }

  // Integrations
  if (selected === "integration:google") return <GooglePanel />;
  if (selected.startsWith("integration:")) return <ComingSoonStub />;

  // Automation
  if (selected === "automation:reminders") return <RemindersPanel />;
  if (selected === "automation:tasks") return <TasksPanel />;
  if (selected === "workspace:control") return <ControlLaunch onOpen={onOpenControl} />;

  // Account
  if (selected === "account:profile") return <AccountPanel />;
  if (selected === "account:usage") return <UsagePanel />;

  return null;
}

function ControlLaunch({ onOpen }: { onOpen?: () => void }) {
  return <div className="flex flex-col gap-4 max-w-xl"><p className="text-[13px]" style={{ color: "var(--text-secondary)" }}>Control is where Smara makes longer work visible: plans, live task activity, approval gates, verified research, integrations, and desktop hand-offs.</p><button onClick={onOpen} className="self-start px-4 py-2.5 rounded-lg text-[13px] font-semibold" style={{ background: "var(--accent)", color: "var(--bg-base)" }}>Open Control panel</button><p className="text-[11px]" style={{ color: "var(--text-muted)" }}>It uses your existing Smara sign-in. No second account or API key is required.</p></div>;
}


/* ── Account panel — profile + plan badge + feedback + theme + sign-out ─── */
function AccountPanel() {
  const account = useAuthStore((s) => s.account);
  const signOut = useAuthStore((s) => s.signOut);
  const { theme, toggle } = useThemeStore();
  const isDark = theme === "dark";
  const plan = (account?.plan || "free").toLowerCase();
  const isFounder = plan === "founder";

  return (
    <div className="flex flex-col gap-5 max-w-md">
      {/* Profile card with plan badge */}
      <div
        className="px-4 py-3.5 rounded-xl flex items-center gap-3"
        style={{ background: "var(--bg-card)", border: "1px solid var(--border-default)" }}
      >
        {account?.avatar_url ? (
          <img
            src={account.avatar_url}
            alt=""
            className="w-12 h-12 rounded-full shrink-0"
            referrerPolicy="no-referrer"
          />
        ) : (
          <div
            className="w-12 h-12 rounded-full flex items-center justify-center text-[15px] font-semibold shrink-0"
            style={{
              background: "var(--accent-soft)",
              color: "var(--accent)",
              border: "1px solid var(--border-accent)",
            }}
          >
            {(account?.display_name || account?.email || "U").trim()[0]?.toUpperCase() || "U"}
          </div>
        )}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <p className="text-[14px] font-medium truncate" style={{ color: "var(--text-primary)" }}>
              {account?.display_name || "—"}
            </p>
            <span
              className="text-[10px] px-1.5 py-0.5 rounded-full uppercase tracking-wider font-semibold"
              style={{
                background: isFounder ? "rgba(168,85,247,0.14)" : "var(--bg-elevated)",
                color: isFounder ? "#a855f7" : "var(--text-muted)",
                border: `1px solid ${isFounder ? "rgba(168,85,247,0.35)" : "var(--border-dim)"}`,
              }}
            >
              {plan}
            </span>
          </div>
          <p className="text-[12px] truncate" style={{ color: "var(--text-muted)" }}>
            {account?.email || ""}
          </p>
        </div>
      </div>

      {/* Feedback */}
      <FeedbackBlock />


      {/* Theme toggle row */}
      <div
        className="px-4 py-3 rounded-xl flex items-center justify-between"
        style={{ background: "var(--bg-card)", border: "1px solid var(--border-dim)" }}
      >
        <div className="flex items-center gap-2.5">
          {isDark ? <Moon size={14} style={{ color: "var(--accent)" }} /> : <Sun size={14} style={{ color: "var(--accent)" }} />}
          <div>
            <p className="text-[13px] font-medium" style={{ color: "var(--text-primary)" }}>
              Appearance
            </p>
            <p className="text-[11px]" style={{ color: "var(--text-muted)" }}>
              {isDark ? "Dark mode" : "Parchment (light)"}
            </p>
          </div>
        </div>
        <button
          onClick={toggle}
          className="px-3 py-1.5 rounded-lg text-[12px] transition-all active:opacity-70"
          style={{
            background: "var(--bg-elevated)",
            border: "1px solid var(--border-default)",
            color: "var(--text-secondary)",
          }}
        >
          Switch to {isDark ? "Light" : "Dark"}
        </button>
      </div>

      {/* Sign out */}
      <button
        onClick={() => signOut()}
        className="flex items-center justify-center gap-2 py-3 rounded-xl text-[13px] font-medium transition-all active:opacity-70 min-h-[44px]"
        style={{
          background: "rgba(239,68,68,0.08)",
          border: "1px solid rgba(239,68,68,0.25)",
          color: "#ef4444",
        }}
      >
        <LogOut size={14} />
        Sign out
      </button>

      <p className="text-[11px] mt-1" style={{ color: "var(--text-muted)" }}>
        Your data stays linked to your Google account. Sign back in any time to pick up exactly where you left off — memories, reminders, tasks, and chat history all persist.
      </p>
    </div>
  );
}

/* ── Usage panel — Free plan caps + live meters; Founder shows "unlimited" ──
 *
 * Single source of truth for what users see about their plan limits. Reads
 * GET /v1/memento/usage which returns plan + per-counter (used, limit, window)
 * snapshots straight from the Redis quota counters.
 */
function UsagePanel() {
  const account = useAuthStore((s) => s.account);
  const plan = (account?.plan || "free").toLowerCase();
  const isFounder = plan === "founder";

  const [usage, setUsage] = useState<Record<string, { used: number; limit: number; window: string }> | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await fetch(`${import.meta.env.VITE_API_URL || ""}/v1/memento/usage`, {
          credentials: "include",
        });
        if (!r.ok) return;
        const data = await r.json();
        if (!cancelled) setUsage(data.usage || null);
      } catch {/* silent — usage strip is non-critical */}
      finally { if (!cancelled) setLoading(false); }
    })();
    return () => { cancelled = true; };
  }, []);

  // Display order + friendly labels + window phrasing.
  const ROWS: [string, string, string][] = [
    ["turns",    "Chat messages",      "per day"],
    ["pdfs",     "PDF generations",    "per day"],
    ["images",   "Image uploads",      "per day"],
    ["ocr",      "OCR (extract text)", "per day"],
    ["docs",     "Document uploads",   "per day"],
    ["searches", "Web + news searches","per day"],
  ];

  if (isFounder) {
    return (
      <div className="flex flex-col gap-4 max-w-xl">
        <div
          className="px-4 py-4 rounded-xl"
          style={{
            background: "rgba(168,85,247,0.08)",
            border: "1px solid rgba(168,85,247,0.30)",
          }}
        >
          <div className="flex items-center gap-2">
            <span
              className="text-[10px] px-1.5 py-0.5 rounded-full uppercase tracking-wider font-semibold"
              style={{
                background: "rgba(168,85,247,0.18)",
                color: "#a855f7",
                border: "1px solid rgba(168,85,247,0.35)",
              }}
            >
              founder
            </span>
            <p className="text-[13px] font-medium" style={{ color: "var(--text-primary)" }}>
              Unlimited on every metered action
            </p>
          </div>
          <p className="mt-2 text-[12px]" style={{ color: "var(--text-muted)" }}>
            Founder accounts have no daily or weekly caps. The Free-plan limits
            below are the same caps every public user sees — shown here for
            reference so you know what the rest of the world is working with.
          </p>
        </div>

        {/* Reference table — what Free users see */}
        <div
          className="px-4 py-3 rounded-xl"
          style={{ background: "var(--bg-card)", border: "1px solid var(--border-dim)" }}
        >
          <p className="text-[11px] uppercase tracking-wider font-semibold mb-2.5" style={{ color: "var(--text-muted)" }}>
            Free-plan caps (reference)
          </p>
          <div className="flex flex-col gap-1.5 text-[12px]">
            {ROWS.map(([k, label, window]) => {
              const row = usage?.[k];
              return (
                <div key={k} className="flex justify-between">
                  <span style={{ color: "var(--text-secondary)" }}>{label}</span>
                  <span style={{ color: "var(--text-muted)" }}>
                    {row ? `${row.limit} ${window}` : "—"}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    );
  }

  // Free user — live meter strip.
  return (
    <div className="flex flex-col gap-4 max-w-xl">
      <div
        className="px-4 py-4 rounded-xl"
        style={{ background: "var(--bg-card)", border: "1px solid var(--border-default)" }}
      >
        <div className="flex items-center justify-between mb-3">
          <p className="text-[13px] font-medium" style={{ color: "var(--text-primary)" }}>
            Today's usage
          </p>
          <span className="text-[10px] uppercase tracking-wider" style={{ color: "var(--text-muted)" }}>
            Free plan · resets at midnight IST
          </span>
        </div>

        {loading && (
          <p className="text-[12px]" style={{ color: "var(--text-muted)" }}>Loading…</p>
        )}

        {!loading && !usage && (
          <p className="text-[12px]" style={{ color: "var(--text-muted)" }}>
            Couldn't load usage right now.
          </p>
        )}

        {!loading && usage && (
          <div className="flex flex-col gap-3">
            {ROWS.map(([k, label, window]) => {
              const row = usage[k];
              if (!row) return null;
              const pct = row.limit ? Math.min(100, Math.round((row.used / row.limit) * 100)) : 0;
              const isHot = pct >= 90;
              const isWarm = pct >= 60;
              return (
                <div key={k}>
                  <div className="flex justify-between text-[12px]" style={{ color: "var(--text-secondary)" }}>
                    <span>{label}</span>
                    <span style={{ color: isHot ? "#ef4444" : "var(--text-muted)" }}>
                      {row.used} / {row.limit} <span style={{ color: "var(--text-dim)" }}>{window}</span>
                    </span>
                  </div>
                  <div className="h-1.5 mt-1 rounded-full" style={{ background: "var(--bg-elevated)" }}>
                    <div
                      className="h-1.5 rounded-full transition-all"
                      style={{
                        width: `${pct}%`,
                        background: isHot ? "#ef4444" : isWarm ? "#f59e0b" : "var(--accent)",
                      }}
                    />
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      <p className="text-[11px] px-1" style={{ color: "var(--text-muted)" }}>
        Hit a cap? Tell us via the feedback button in Profile — we're listening.
        Paid plans with higher caps are coming soon.
      </p>
    </div>
  );
}


/* ── Feedback block — anyone can send a note to the founder ──────────────── */
function FeedbackBlock() {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [status, setStatus] = useState<null | "sent" | "error">(null);
  const [errorMsg, setErrorMsg] = useState<string>("");

  async function send() {
    const trimmed = text.trim();
    setStatus(null);
    setErrorMsg("");

    // Client-side validation matching backend (Pydantic min_length=3 / max_length=4000)
    if (trimmed.length < 3) {
      setStatus("error");
      setErrorMsg("Type at least 3 characters.");
      return;
    }
    if (trimmed.length > 4000) {
      setStatus("error");
      setErrorMsg("Feedback is too long (max 4000 characters).");
      return;
    }
    if (sending) return;

    setSending(true);
    try {
      // Use apiClient — same auth + error path as the rest of the app.
      // ApiError carries the backend's detail string so the user sees the
      // actual reason instead of a generic "Couldn't send".
      await apiClient.post("/v1/memento/feedback", {
        message: trimmed, channel: "web",
      });
      setStatus("sent");
      setText("");
      setTimeout(() => { setStatus(null); setOpen(false); }, 1800);
    } catch (e) {
      setStatus("error");
      if (e instanceof ApiError) {
        if (e.status === 401) setErrorMsg("Your session expired — sign in again.");
        else if (e.status === 503) setErrorMsg("Feedback service is down — try later.");
        else if (e.status === 422) setErrorMsg(e.detail || "Validation failed.");
        else setErrorMsg(e.detail || `Couldn't send (${e.status}).`);
      } else {
        setErrorMsg("Network error — check your connection.");
      }
    } finally {
      setSending(false);
    }
  }

  return (
    <div
      className="px-4 py-3 rounded-xl"
      style={{ background: "var(--bg-card)", border: "1px solid var(--border-dim)" }}
    >
      <div className="flex items-center justify-between">
        <div>
          <p className="text-[13px] font-medium" style={{ color: "var(--text-primary)" }}>
            Send feedback
          </p>
          <p className="text-[11px]" style={{ color: "var(--text-muted)" }}>
            Bugs, ideas, anything — goes straight to the founders.
          </p>
        </div>
        <button
          onClick={() => setOpen((v) => !v)}
          className="px-3 py-1.5 rounded-lg text-[12px] transition-all active:opacity-70"
          style={{
            background: "var(--bg-elevated)",
            border: "1px solid var(--border-default)",
            color: "var(--text-secondary)",
          }}
        >
          {open ? "Close" : "Write a note"}
        </button>
      </div>
      {open && (
        <div className="mt-3">
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={4}
            maxLength={4000}
            placeholder="What's working, what's not, or what you'd love to see…"
            className="w-full text-[13px] rounded-lg px-3 py-2 outline-none"
            style={{
              background: "var(--bg-elevated)",
              border: "1px solid var(--border-default)",
              color: "var(--text-primary)",
            }}
          />
          <div className="mt-2 flex items-center justify-between">
            <span className="text-[11px]" style={{ color: status === "error" ? "#ef4444" : status === "sent" ? "var(--accent)" : "var(--text-muted)" }}>
              {status === "sent"
                ? "Thanks — we'll read it."
                : status === "error"
                ? (errorMsg || "Couldn't send. Try again.")
                : `${text.length}/4000`}
            </span>
            <button
              onClick={send}
              disabled={!text.trim() || sending}
              className="px-3 py-1.5 rounded-lg text-[12px] font-semibold transition-all active:opacity-70 disabled:opacity-50"
              style={{
                background: "var(--accent)",
                color: "var(--bg-base)",
              }}
            >
              {sending ? "Sending…" : "Send"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}


function ComingSoonStub() {
  return (
    <div
      className="px-4 py-6 rounded-xl text-center max-w-md"
      style={{ background: "var(--bg-card)", border: "1px dashed var(--border-default)" }}
    >
      <p className="text-[13px]" style={{ color: "var(--text-secondary)" }}>
        Coming soon.
      </p>
    </div>
  );
}
