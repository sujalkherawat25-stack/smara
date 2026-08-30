import { useState } from "react";
import { useChatStore, type ActivityItem, type AgentPhase } from "@/stores/chatStore";
import Markdown from "./Markdown";
import SmaraLogo from "@/components/SmaraLogo";
import {
  ChevronDown, ChevronRight, Wrench, AlertTriangle,
  ShieldAlert, ShieldCheck, ShieldX, Loader2, Sparkles,
} from "lucide-react";

// `useState` is used by the activity-feed collapse + ConfirmRowCard.
// `ChevronDown` / `ChevronRight` are used by the top-level collapse toggle.

// ── ACTIVITY-FEED POLICY ─────────────────────────────────────────────────────
// The activity feed must NEVER show internal model reasoning or raw tool
// output. Two reasons:
//   1. The reason-act brain (Gemini) and the writer (Sarvam) are different
//      models with different policies. Showing Gemini's intermediate text
//      (refusals, hedges, scratch thoughts) created a trust bug: users saw
//      "I can't search for X" in the activity feed and then a fabricated
//      answer in the final reply — making Smara look like a liar.
//   2. Raw tool output (web-search snippets, OCR text dumps) belongs in the
//      synthesized final reply with proper citations — not as raw blobs in
//      the activity feed where it's easy to misread or quote wrongly.
//
// What we DO show: phase ("Working on it…"), tool calls ("Web search · query"),
// confirmation cards, and errors. No tool result previews. No thoughts.
// ─────────────────────────────────────────────────────────────────────────────

const PHASE_LABEL: Record<AgentPhase, string> = {
  triage: "Thinking…",
  retrieve: "Thinking…",
  reason_act: "Working on it…",
  answer: "Writing reply…",
};

// Human-readable tool names for the activity feed.
const TOOL_LABEL: Record<string, string> = {
  web_search: "Web search",
  "research.deep": "Deep research",
  news_search: "News search",
  fetch_url: "Read page",
  url_summarize: "Summarise page",
  flashcards: "Flashcards",
  code_write: "Write code",
  code_explain: "Explain code",
  code_review: "Review code",
  get_weather: "Weather",
  get_current_time: "Current time",
  calculate: "Calculate",
  recall_memory: "Recall memory",
  save_memory: "Save memory",
  update_memory: "Update memory",
  forget_memory: "Forget memory",
  read_document: "Read document",
  generate_pdf: "Generate PDF",
  edit_pdf: "Edit PDF",
  set_reminder: "Set reminder",
  schedule_task: "Schedule task",
  recall_actions: "Recall actions",
  read_emails: "Read emails",
  send_email: "Send email",
  list_calendar_events: "Calendar",
  create_calendar_event: "Create event",
};

// Pull the most meaningful single arg from a tool call to show inline.
function mainArg(_name: string, args: Record<string, unknown>): string | null {
  // Order matters — first hit wins.
  const candidates = [
    "query", "url", "expression", "city", "email", "topic",
    "prompt", "task", "text",
  ];
  for (const k of candidates) {
    if (typeof args[k] === "string" && (args[k] as string).length > 0) {
      const v = args[k] as string;
      return v.length > 60 ? v.slice(0, 57) + "…" : v;
    }
  }
  return null;
}

export default function StreamingMessage() {
  const text = useChatStore((s) => s.streamingText);
  const phase = useChatStore((s) => s.currentPhase);
  const activity = useChatStore((s) => s.activity);
  const status = [...activity].reverse().find(
    (item): item is Extract<ActivityItem, { kind: "status" }> => item.kind === "status"
  );

  const [expanded, setExpanded] = useState(true);

  // Filter out memory_search — internal plumbing, not user-facing.
  // Merge consecutive tool_call + tool_result pairs into single entries.
  const rows = buildRows(activity);
  const hasRows = rows.length > 0;
  const hasText = text.length > 0;
  const showHeader = hasRows || !!phase;

  return (
    <div className="flex gap-3 animate-fade-in">
      <div className="shrink-0 mt-0.5">
        <SmaraLogo size={24} />
      </div>

      <div className="flex-1 min-w-0">
        {showHeader && (
          <div
            className="mb-3 rounded-xl text-xs"
            style={{ background: "var(--bg-elevated)", border: "1px solid var(--border-dim)" }}
          >
            <button
              onClick={() => setExpanded((e) => !e)}
              className="w-full flex items-center gap-2 px-3 py-2 text-left transition-colors"
              style={{ color: "var(--text-secondary)" }}
            >
              {expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
              <span className="font-medium">
                {status?.label || (phase ? PHASE_LABEL[phase] : "Done")}
              </span>
              {!expanded && hasRows && (
                <span style={{ color: "var(--text-dim)" }}>
                  · {rows.length} {rows.length === 1 ? "step" : "steps"}
                </span>
              )}
              {!hasText && (
                <span
                  className="ml-auto w-1.5 h-1.5 rounded-full"
                  style={{ background: "var(--accent)", animation: "pulse 1.4s infinite" }}
                />
              )}
            </button>

            {status?.detail && (
              <div
                className="px-3 pb-2 text-[11px]"
                style={{ color: "var(--text-muted)" }}
              >
                {status.detail}
              </div>
            )}
            {typeof status?.progress === "number" && (
              <div className="h-0.5 overflow-hidden" style={{ background: "var(--border-dim)" }}>
                <div
                  className="h-full transition-all duration-300"
                  style={{
                    width: `${Math.max(0, Math.min(100, status.progress))}%`,
                    background: "var(--accent)",
                  }}
                />
              </div>
            )}

            {expanded && hasRows && (
              <div
                className="px-3 pb-2.5 space-y-1"
                style={{ borderTop: "1px solid var(--border-dim)" }}
              >
                {rows.map((row, i) => <FeedRow key={i} row={row} />)}
              </div>
            )}
          </div>
        )}

        {hasText ? (
          <div className="text-sm leading-7" style={{ color: "var(--text-primary)" }}>
            <Markdown>{text}</Markdown>
          </div>
        ) : !hasRows ? (
          <div className="flex items-center gap-1.5 py-2">
            {[0, 150, 300].map((delay) => (
              <span
                key={delay}
                className="w-1.5 h-1.5 rounded-full dot-bounce"
                style={{ background: "var(--accent)", opacity: 0.4, animationDelay: `${delay}ms` }}
              />
            ))}
          </div>
        ) : null}
      </div>
    </div>
  );
}

// ── Row types after merging ───────────────────────────────────────────────────

type ToolRow = {
  kind: "tool";
  name: string;
  args: Record<string, unknown>;
  result?: { ok: boolean; summary?: string };   // status-only, except safe research progress
};
type ErrorRow = { kind: "error"; message: string; recoverable: boolean };
type ConfirmRow = { kind: "confirm"; item: Extract<ActivityItem, { kind: "confirm_request" }> };
type SkillRow = { kind: "skill"; item: Extract<ActivityItem, { kind: "skill_proposal" }> };
type FeedRowType = ToolRow | ErrorRow | ConfirmRow | SkillRow;

// A few older model turns attempted a planner-only PDF block builder which
// is not an executable tool. It may appear in a historical/replayed stream
// next to a successful Generate PDF call. Never show that internal failed
// attempt as a red product error; the real tool row is the useful status.
function isHiddenInternalToolAttempt(name: string): boolean {
  return name.replace(/[^a-z]/gi, "").toLowerCase() === "generatepdfblocks";
}

function buildRows(activity: ActivityItem[]): FeedRowType[] {
  const out: FeedRowType[] = [];
  for (let i = 0; i < activity.length; i++) {
    const item = activity[i];
    // Hidden by policy (see header comment): internal reasoning text and
    // memory-search probes are not surfaced. The activity feed shows ONLY
    // actions the agent took (tool calls + outcomes), never the model's
    // intermediate text.
    if (item.kind === "memory_search") continue;
    if (item.kind === "thought") continue;
    if (item.kind === "status") continue;
    if (
      (item.kind === "tool_call" || item.kind === "tool_result") &&
      isHiddenInternalToolAttempt(item.name)
    ) continue;

    if (item.kind === "tool_call") {
      // Look ahead for the matching tool_result (same name, next occurrence).
      const resultIdx = activity.findIndex(
        (a, j) => j > i && a.kind === "tool_result" && a.name === item.name
      );
      const result =
        resultIdx !== -1
          ? (activity[resultIdx] as Extract<ActivityItem, { kind: "tool_result" }>)
          : undefined;
      out.push({
        kind: "tool",
        name: item.name,
        args: item.args,
        // We only carry the ok/fail status forward — NOT the preview text
        // or citations. Citations land on the assistant message as the
        // Sources card (see MessageBubble). Raw preview text is suppressed
        // entirely to avoid streaming/final mismatch leaks.
        // A deep research pass has no sensitive raw output in its preview;
        // retain only the bounded source-count summary so the user can see
        // that Smara actually tried multiple angles and pages.
        result: result
          ? {
              ok: result.ok,
              summary: item.name === "research.deep" ? result.preview : undefined,
            }
          : undefined,
      });
      continue;
    }
    // Skip tool_result — consumed by the tool_call look-ahead above.
    if (item.kind === "tool_result") continue;
    if (item.kind === "error") {
      out.push({ kind: "error", message: item.message, recoverable: item.recoverable });
      continue;
    }
    if (item.kind === "confirm_request") {
      out.push({ kind: "confirm", item });
      continue;
    }
    if (item.kind === "skill_proposal") {
      out.push({ kind: "skill", item });
      continue;
    }
  }
  return out;
}

// ── Row renderer ──────────────────────────────────────────────────────────────

function FeedRow({ row }: { row: FeedRowType }) {
  if (row.kind === "error") {
    return (
      <div
        className="flex gap-2 items-start pt-1"
        style={{ color: row.recoverable ? "var(--text-muted)" : "#f87171" }}
      >
        <AlertTriangle size={11} className="mt-1 shrink-0" />
        <span className="leading-5">{row.message}</span>
      </div>
    );
  }

  if (row.kind === "confirm") {
    return <ConfirmRowCard item={row.item} />;
  }
  if (row.kind === "skill") {
    return <SkillProposalRow item={row.item} />;
  }

  // ── Tool row ──────────────────────────────────────────────────────────────
  // Status-only by policy (see header). No preview text, no citations
  // surfaced here — citations land on the assistant message as Sources card.
  const { name, args, result } = row;
  const label = TOOL_LABEL[name] ?? name.replace(/_/g, " ");
  const arg = mainArg(name, args);
  const pending = result === undefined;

  return (
    <div className="pt-1">
      <div
        className="w-full flex items-center gap-2"
        style={{ color: "var(--text-muted)" }}
      >
        {/* Status indicator */}
        {pending ? (
          <Loader2
            size={11}
            className="shrink-0 animate-spin mt-0.5"
            style={{ color: "var(--accent)" }}
          />
        ) : (
          <Wrench
            size={11}
            className="shrink-0 mt-0.5"
            style={{ color: result.ok ? "var(--accent2)" : "#f87171" }}
          />
        )}

        {/* Tool name */}
        <span className="font-medium" style={{ color: "var(--text-secondary)" }}>
          {label}
        </span>

        {/* Main argument pill — kept because the argument is what the USER
            asked for, not internal reasoning. Safe to show. */}
        {arg && (
          <>
            <span style={{ color: "var(--text-dim)" }}>·</span>
            <span
              className="truncate max-w-[260px]"
              style={{ color: "var(--text-dim)" }}
            >
              {arg}
            </span>
          </>
        )}
      </div>
      {result?.summary && (
        <div className="pl-5 pt-0.5 text-[10px]" style={{ color: "var(--text-dim)" }}>
          {result.summary}
        </div>
      )}
    </div>
  );
}

function SkillProposalRow({
  item,
}: {
  item: Extract<ActivityItem, { kind: "skill_proposal" }>;
}) {
  const respond = useChatStore((s) => s.respondSkillProposal);
  const pending = item.status === "pending";
  return (
    <div
      className="rounded-lg px-3 py-2.5 mt-1"
      style={{ background: "var(--accent-soft)", border: "1px solid var(--border-accent)" }}
    >
      <div className="flex items-start gap-2">
        <Sparkles size={13} className="mt-0.5 shrink-0" style={{ color: "var(--accent)" }} />
        <div className="min-w-0">
          <div className="font-medium" style={{ color: "var(--text-primary)" }}>
            {item.status === "approved" ? "Workflow learned" : "Reuse this workflow?"}
          </div>
          <div className="mt-0.5" style={{ color: "var(--text-muted)" }}>
            {item.name}
          </div>
          {pending ? (
            <div className="flex gap-2 mt-2">
              <button
                onClick={() => void respond(item.id, true)}
                className="rounded-md px-3 py-1 font-medium"
                style={{ background: "var(--accent)", color: "var(--bg-base)" }}
              >
                Activate
              </button>
              <button
                onClick={() => void respond(item.id, false)}
                className="rounded-md px-3 py-1"
                style={{ border: "1px solid var(--border-dim)", color: "var(--text-secondary)" }}
              >
                Not now
              </button>
            </div>
          ) : (
            <div className="mt-1" style={{ color: "var(--text-secondary)" }}>
              {item.status === "approved" ? "Smara will reuse it when relevant" : "Not activated"}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ── J1: confirm card ──────────────────────────────────────────────────────────

function ConfirmRowCard({ item }: { item: Extract<ActivityItem, { kind: "confirm_request" }> }) {
  const respond = useChatStore((s) => s.respondConfirmation);

  if (item.status === "pending") {
    return (
      <div
        className="rounded-lg px-3 py-2.5 space-y-2 mt-1"
        style={{ background: "rgba(251,191,36,0.07)", border: "1px solid rgba(251,191,36,0.3)" }}
      >
        <div className="flex gap-2 items-start" style={{ color: "var(--text-primary)" }}>
          <ShieldAlert size={13} className="mt-0.5 shrink-0" style={{ color: "#fbbf24" }} />
          <span className="leading-5">
            Approval needed:{" "}
            <span style={{ color: "var(--text-secondary)" }}>{item.preview}</span>
          </span>
        </div>
        <div className="flex gap-2 pl-5">
          <button
            onClick={() => respond(item.id, true)}
            className="px-3 py-1 rounded-md text-xs font-medium transition-colors"
            style={{ background: "var(--accent)", color: "var(--bg-base, #0a0918)" }}
          >
            Approve
          </button>
          <button
            onClick={() => respond(item.id, false)}
            className="px-3 py-1 rounded-md text-xs font-medium transition-colors"
            style={{ background: "transparent", border: "1px solid var(--border-dim)", color: "var(--text-secondary)" }}
          >
            Deny
          </button>
        </div>
      </div>
    );
  }

  const approved = item.status === "approved";
  const Icon = approved ? ShieldCheck : ShieldX;
  const label =
    item.status === "approved" ? "Approved" :
    item.status === "denied" ? "Denied — action skipped" :
    "No response — action skipped";
  return (
    <div className="flex gap-2 items-start pt-1" style={{ color: "var(--text-muted)" }}>
      <Icon size={11} className="mt-1 shrink-0" style={{ color: approved ? "var(--accent2)" : "#f87171" }} />
      <span className="leading-5">
        <span className="font-mono" style={{ color: "var(--text-secondary)" }}>{item.name}</span>
        {" · "}{label}
      </span>
    </div>
  );
}
