/**
 * SlashCommandPalette.tsx — Claude Code-style command palette.
 *
 * Appears above the chat input when the user types "/" at the start of
 * a message. Each command pre-fills the input with a template prompt so
 * the user just fills in the blanks and sends.
 *
 * Behaviour:
 *   • Trigger:        first char of input is "/", and there's no later text
 *                     that isn't a continuation of the command name.
 *   • Filter:         text after "/" filters the list (case-insensitive,
 *                     subsequence match on name).
 *   • Navigate:       ↑/↓ to move selection, Enter to commit, Esc to dismiss.
 *   • Commit:         input value is replaced with the command's template,
 *                     caret positioned where the user should start typing.
 *
 * Adding a command = one entry in COMMANDS below. Keep templates short and
 * use {placeholder} markers — the palette places the caret at the first
 * placeholder it finds.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import type { MouseEvent } from "react";
import {
  Brain, Bug, Calendar, Code, FileText, Mail,
  MessageSquare, NotebookPen, Pencil, Scissors, Search,
  Sparkles, Trash2, Wrench, Bell, Newspaper, FileCheck, Languages,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

export interface SlashCommand {
  /** Slug after "/" — what the user types to filter to this command. */
  name: string;
  /** Short description, shown next to the name in the palette row. */
  description: string;
  /** Longer usage note shown in the hover tooltip (Claude Code style) —
   *  what it actually does, when to reach for it, what it needs from you. */
  usage: string;
  /** Lucide icon shown at the left. */
  icon: LucideIcon;
  /** Pre-fill template. The first {placeholder} marker becomes the caret pos. */
  template: string;
  /** Category label for visual grouping. */
  category: "Build" | "Code" | "Info" | "Memory" | "Action" | "Smara";
}

export const COMMANDS: SlashCommand[] = [
  // ── Build ────────────────────────────────────────────────────────────────
  {
    name: "pdf",
    description: "Generate a downloadable PDF report",
    usage:
      "Turns notes, a summary, or a topic into a formatted PDF — headings, " +
      "bullets, tables, whatever the content calls for. One shot, no draft " +
      "step. Tell it what to put in the document.",
    icon: FileText,
    category: "Build",
    template: "Generate a PDF report on {topic or paste content}",
  },
  {
    name: "edit-pdf",
    description: "Merge, split, rotate, or watermark a PDF",
    usage:
      "Edits a PDF you already have — attach it first (paperclip button), " +
      "then tell it what to do: merge with another attached PDF, extract a " +
      "page range, rotate pages, or stamp a watermark across every page.",
    icon: Scissors,
    category: "Build",
    template: "{Merge / split / rotate / watermark} the attached PDF: {details}",
  },
  {
    name: "flashcards",
    description: "Generate Anki-importable study cards",
    usage:
      "Turns notes, an article, or pasted text into a CSV of question/answer " +
      "flashcards you can import straight into Anki. Tell it how many cards and " +
      "paste the source material.",
    icon: NotebookPen,
    category: "Build",
    template: "Make {N} flashcards from this:\n\n{content}",
  },

  // ── Code ─────────────────────────────────────────────────────────────────
  {
    name: "code",
    description: "Write code with a plan first",
    usage:
      "Writes code with a short plan up front instead of jumping straight to " +
      "output. Tell it what the code should do and the language.",
    icon: Code,
    category: "Code",
    template: "Write code that {does what}. Language: {python|typescript|…}.",
  },
  {
    name: "explain",
    description: "Plain-English walkthrough of code",
    usage:
      "Paste a code block and get a plain-English walkthrough of what it does " +
      "and why — no jargon dump, aimed at actually understanding it.",
    icon: Sparkles,
    category: "Code",
    template: "Explain this code:\n\n```\n{paste code}\n```",
  },
  {
    name: "review",
    description: "Senior-engineer review with severity tags",
    usage:
      "Reviews pasted code like a senior engineer — flags real bugs and risks " +
      "with severity tags, not style nitpicks. Paste the code block to review.",
    icon: Bug,
    category: "Code",
    template: "Review this code for bugs:\n\n```\n{paste code}\n```",
  },

  // ── Info ─────────────────────────────────────────────────────────────────
  {
    name: "summarise",
    description: "tl;dr of a web page",
    usage: "Fetches a URL and gives you the short version — key points only.",
    icon: FileCheck,
    category: "Info",
    template: "Summarise {url}",
  },
  {
    name: "search",
    description: "Web search",
    usage: "Runs a live web search and answers from real, current results — not just training data.",
    icon: Search,
    category: "Info",
    template: "Search the web for {query}",
  },
  {
    name: "news",
    description: "Latest news on a topic",
    usage: "Pulls recent headlines and articles on a topic, with sources cited.",
    icon: Newspaper,
    category: "Info",
    template: "What's the latest news on {topic}",
  },
  {
    name: "translate",
    description: "Translate text",
    usage: "Translates pasted text into any language you specify.",
    icon: Languages,
    category: "Info",
    template: "Translate this to {language}:\n\n{text}",
  },

  // ── Memory ───────────────────────────────────────────────────────────────
  {
    name: "remember",
    description: "Save a fact to long-term memory",
    usage:
      "Explicitly saves a fact to long-term memory so it's recalled in any " +
      "future conversation — Smara also picks up facts automatically as you " +
      "chat, but use this when you want to be sure something specific sticks.",
    icon: Brain,
    category: "Memory",
    template: "Remember that {fact}",
  },
  {
    name: "forget",
    description: "Remove a stored fact",
    usage: "Removes a specific fact from long-term memory — the opposite of /remember.",
    icon: Trash2,
    category: "Memory",
    template: "Forget that {fact to remove}",
  },
  {
    name: "recall",
    description: "What you know about a topic / person",
    usage: "Searches your stored memories for everything Smara knows about a topic, person, or project.",
    icon: Pencil,
    category: "Memory",
    template: "What do you know about {topic or person}?",
  },
  // ── Action ───────────────────────────────────────────────────────────────
  {
    name: "remind",
    description: "Set a reminder",
    usage: "Sets a one-off or recurring reminder — you'll get pinged on your best-connected channel when it fires.",
    icon: Bell,
    category: "Action",
    template: "Remind me at {time} to {what}",
  },
  {
    name: "schedule",
    description: "Recurring agent task",
    usage: "Schedules Smara to run a task on a recurring cadence (e.g. every morning) and deliver the result to you.",
    icon: Calendar,
    category: "Action",
    template: "Every {when, e.g. morning at 9} {do what}",
  },
  {
    name: "email",
    description: "Search Gmail (requires connection)",
    usage:
      "Searches your connected Gmail inbox. Requires connecting Google in " +
      "Settings first — sending email requires your explicit confirmation " +
      "every time.",
    icon: Mail,
    category: "Action",
    template: "Show me my unread emails from {sender or topic}",
  },

  // ── Smara meta ───────────────────────────────────────────────────────────
  {
    name: "feedback",
    description: "Send feedback straight to the founders (web + Telegram)",
    usage: "Sends your message directly to the founders' inbox — bugs, ideas, anything. Bypasses the normal chat/agent flow.",
    icon: MessageSquare,
    category: "Smara",
    // The /feedback prefix is preserved verbatim — chatStore.send()
    // intercepts messages starting with "/feedback " and routes them to
    // the dedicated feedback API instead of the agent loop. Same pattern
    // as the Telegram worker's /feedback handler.
    template: "/feedback {tell us anything — bugs, ideas, what you'd love}",
  },
  {
    name: "help",
    description: "What can Smara do?",
    usage: "Asks Smara to walk you through what it can help with — a good starting point if you're not sure what to try.",
    icon: Wrench,
    category: "Smara",
    template: "What can you help me with?",
  },
];

interface Props {
  /** Current input box value. */
  value: string;
  /** Called when a command is committed — pass the new input value + caret
   *  pos + the committed command's name (e.g. "resume") so callers can react
   *  to which flow just started, without re-parsing the template text. */
  onCommit: (newValue: string, caretAt: number, commandName: string) => void;
  /** Called when the user dismisses with Esc. */
  onDismiss: () => void;
}

/** Pull the slash-token from the input. Returns null when the palette
 *  should NOT be shown (no leading slash, or text has progressed past
 *  the command name into a message body). */
export function extractSlashQuery(value: string): string | null {
  if (!value.startsWith("/")) return null;
  // Treat anything from "/" up to the first whitespace as the command query.
  // Once the user types a space, they've committed to a free-form message
  // and the palette should disappear.
  const space = value.search(/\s/);
  if (space === -1) return value.slice(1);  // "/res" -> "res"
  return null;
}

/** Simple subsequence match — "rsm" matches "resume". */
function subseqMatch(name: string, query: string): boolean {
  if (!query) return true;
  let i = 0;
  for (const c of name.toLowerCase()) {
    if (c === query[i]) i++;
    if (i === query.length) return true;
  }
  return i === query.length;
}

/** Compute caret position inside the template after the first {…} marker. */
function caretFromTemplate(template: string): { value: string; caret: number } {
  const m = template.match(/\{[^}]+\}/);
  if (!m) return { value: template, caret: template.length };
  const before = template.slice(0, m.index!);
  const after = template.slice(m.index! + m[0].length);
  // We replace the {placeholder} with empty string so the user types in
  // its place. Caret lands where the placeholder started.
  return { value: before + after, caret: before.length };
}

export default function SlashCommandPalette({ value, onCommit, onDismiss }: Props) {
  const query = (extractSlashQuery(value) || "").toLowerCase();

  const filtered = useMemo(() => {
    return COMMANDS.filter((c) => subseqMatch(c.name, query));
  }, [query]);

  const [selected, setSelected] = useState(0);
  useEffect(() => { setSelected(0); }, [query]);

  // Hover tooltip (Claude Code style) — tracks the hovered row's viewport
  // rect so the tooltip can render `fixed` and escape the scrollable list's
  // clipped overflow-x. Prefers sitting to the right of the row; clamped
  // (not flipped) to stay fully on-screen on narrow viewports, where a hard
  // flip to the left would just push it off the opposite edge instead.
  const TOOLTIP_WIDTH = 288; // matches w-72
  const [hover, setHover] = useState<{ cmd: SlashCommand; top: number; left: number } | null>(null);

  function showHover(e: MouseEvent<HTMLDivElement>, cmd: SlashCommand) {
    const rect = e.currentTarget.getBoundingClientRect();
    const desired = rect.right + 8;
    const left = Math.min(desired, window.innerWidth - TOOLTIP_WIDTH - 8);
    const top = Math.min(rect.top, window.innerHeight - 140);
    setHover({ cmd, top, left: Math.max(8, left) });
  }

  const listRef = useRef<HTMLDivElement>(null);

  // Keep selected row scrolled into view as user arrows around.
  useEffect(() => {
    const el = listRef.current?.querySelector<HTMLDivElement>(`[data-idx="${selected}"]`);
    el?.scrollIntoView({ block: "nearest" });
  }, [selected]);

  // Keyboard handling — attaches to window because the input also has its
  // own key handler. We rely on capture phase + stopPropagation when we
  // act, so the input's enter/escape don't fire.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (filtered.length === 0) {
        if (e.key === "Escape") { e.preventDefault(); onDismiss(); }
        return;
      }
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setSelected((i) => (i + 1) % filtered.length);
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setSelected((i) => (i - 1 + filtered.length) % filtered.length);
      } else if (e.key === "Enter") {
        e.preventDefault();
        e.stopPropagation();
        commit(filtered[selected]);
      } else if (e.key === "Escape") {
        e.preventDefault();
        onDismiss();
      } else if (e.key === "Tab") {
        e.preventDefault();
        commit(filtered[selected]);
      }
    }
    window.addEventListener("keydown", onKey, { capture: true });
    return () => window.removeEventListener("keydown", onKey, { capture: true } as any);
  }, [filtered, selected]);  // eslint-disable-line react-hooks/exhaustive-deps

  function commit(cmd: SlashCommand) {
    const { value: v, caret } = caretFromTemplate(cmd.template);
    onCommit(v, caret, cmd.name);
  }

  if (filtered.length === 0) {
    return (
      <div
        className="absolute bottom-full left-0 right-0 mb-2 mx-auto max-w-2xl rounded-xl px-3 py-2 text-[12px]"
        style={{
          background: "var(--bg-surface)",
          border: "1px solid var(--border-default)",
          color: "var(--text-muted)",
          boxShadow: "var(--shadow-md, 0 4px 16px rgba(0,0,0,0.15))",
        }}
      >
        No commands match <span style={{ color: "var(--text-secondary)" }}>/{query}</span>. Press Esc to dismiss.
      </div>
    );
  }

  return (
    <div
      className="absolute bottom-full left-0 right-0 mb-2 mx-auto max-w-2xl rounded-xl overflow-hidden"
      style={{
        background: "var(--bg-surface)",
        border: "1px solid var(--border-default)",
        boxShadow: "var(--shadow-md, 0 4px 16px rgba(0,0,0,0.15))",
      }}
    >
      <div
        ref={listRef}
        className="max-h-[280px] overflow-y-auto py-1"
        role="listbox"
        aria-label="Slash commands"
      >
        {filtered.map((cmd, i) => {
          const Icon = cmd.icon;
          const active = i === selected;
          return (
            <div
              key={cmd.name}
              data-idx={i}
              role="option"
              aria-selected={active}
              onMouseEnter={(e) => { setSelected(i); showHover(e, cmd); }}
              onMouseLeave={() => setHover(null)}
              onClick={() => commit(cmd)}
              className="flex items-center gap-3 px-3 py-2 cursor-pointer transition-colors"
              style={{
                background: active ? "var(--bg-elevated)" : "transparent",
              }}
            >
              <div
                className="shrink-0 grid place-items-center w-7 h-7 rounded-lg"
                style={{
                  background: active ? "var(--accent-soft)" : "var(--bg-elevated)",
                  color: active ? "var(--accent)" : "var(--text-muted)",
                }}
              >
                <Icon size={13} />
              </div>
              <div className="flex-1 min-w-0 flex items-baseline gap-2">
                <span
                  className="text-[13px] font-medium"
                  style={{ color: "var(--text-primary)" }}
                >
                  /{cmd.name}
                </span>
                <span
                  className="text-[10px] uppercase tracking-wide"
                  style={{ color: "var(--text-dim)" }}
                >
                  {cmd.category}
                </span>
              </div>
            </div>
          );
        })}
      </div>
      <div
        className="px-3 py-1.5 text-[10px] flex items-center gap-3"
        style={{
          background: "var(--bg-elevated)",
          color: "var(--text-dim)",
          borderTop: "1px solid var(--border-dim)",
        }}
      >
        <span>↑↓ to navigate</span>
        <span>Enter / Tab to select</span>
        <span>Esc to dismiss</span>
      </div>
      {hover && (
        <div
          className="fixed z-50 w-72 rounded-lg px-3 py-2.5 text-[12px] leading-relaxed pointer-events-none"
          style={{
            top: hover.top,
            left: hover.left,
            background: "var(--bg-elevated)",
            border: "1px solid var(--border-default)",
            color: "var(--text-secondary)",
            boxShadow: "var(--shadow-md, 0 4px 16px rgba(0,0,0,0.25))",
          }}
        >
          <div className="text-[11px] font-medium uppercase tracking-wide mb-1" style={{ color: "var(--text-dim)" }}>
            /{hover.cmd.name}
          </div>
          {hover.cmd.usage}
        </div>
      )}
    </div>
  );
}
