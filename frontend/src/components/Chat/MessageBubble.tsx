import { useState } from "react";
import { Brain, Check, ChevronDown, ChevronUp, Copy, Download, Eye, ExternalLink, FileText, Link2, Pencil, Sparkles } from "lucide-react";
import RetrievalPanel from "./RetrievalPanel";
import Markdown from "./Markdown";
import MermaidDiagram from "./MermaidDiagram";
import SmaraLogo from "@/components/SmaraLogo";
import { usePdfPreviewStore } from "@/stores/pdfPreviewStore";
import { useChatStore } from "@/stores/chatStore";
import type { ConversationMessage } from "@/types/memory";
import clsx from "clsx";

/** Copy text to clipboard with a graceful fallback for old browsers. */
async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
    // Fallback for older browsers / non-https dev contexts.
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return ok;
  } catch {
    return false;
  }
}

/** Best-effort display label for a citation. Prefer the host (and path tail)
 *  for http(s) URLs; fall back to the raw string for other identifiers. */
function citationLabel(c: string): { label: string; href: string | null } {
  const trimmed = c.trim();
  if (!trimmed) return { label: "", href: null };
  try {
    const u = new URL(trimmed);
    if (u.protocol === "http:" || u.protocol === "https:") {
      const host = u.hostname.replace(/^www\./, "");
      // Show host + a short path hint when the path is non-trivial.
      const pathTail = u.pathname && u.pathname !== "/"
        ? u.pathname.length > 28 ? "…" + u.pathname.slice(-26) : u.pathname
        : "";
      return { label: host + pathTail, href: trimmed };
    }
  } catch {
    /* not a URL — fall through */
  }
  return { label: trimmed.length > 60 ? trimmed.slice(0, 57) + "…" : trimmed, href: null };
}

interface Props {
  message: ConversationMessage;
  /** Inserts a visual-node follow-up into the composer so the next query can
   * continue from the exact part of a diagram the user clicked. */
  onNodeAsk?: (prompt: string) => void;
  /** Called when the user clicks the Edit pencil on their own message —
   *  parent should drop the text into the input box for re-send. */
  onEdit?: (text: string) => void;
}

export default function MessageBubble({ message, onEdit, onNodeAsk }: Props) {
  const [showTrace, setShowTrace] = useState(false);
  const [copied, setCopied] = useState(false);
  const isUser = message.role === "user";
  const respondSkillProposal = useChatStore((s) => s.respondSkillProposal);

  async function handleCopy() {
    const ok = await copyText(message.content);
    if (ok) {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    }
  }

  if (isUser) {
    return (
      <div className="flex justify-end animate-fade-in">
        <div className="flex flex-col items-end gap-1 max-w-[88%] sm:max-w-[80%]">
          <div
            className="rounded-2xl rounded-br-md px-4 py-2.5 text-sm leading-6 whitespace-pre-wrap break-words"
            style={{
              background: "var(--user-bubble-bg)",
              border: "1px solid var(--user-bubble-border)",
              color: "var(--text-primary)",
            }}
          >
            {message.content}
          </div>
          {/* Edit + Copy — always visible (dim), brighten on hover. Matches
              ChatGPT/Claude pattern and works on mobile (no hover). */}
          <div
            className="flex items-center gap-1 pr-1 opacity-60 hover:opacity-100 transition-opacity duration-150"
            style={{ color: "var(--text-muted)" }}
          >
            {onEdit && (
              <button
                onClick={() => onEdit(message.content)}
                className="grid place-items-center w-7 h-7 rounded-lg transition-colors duration-150 hover:bg-[var(--bg-elevated)]"
                title="Edit and resend"
                aria-label="Edit message"
              >
                <Pencil size={12} />
              </button>
            )}
            <button
              onClick={handleCopy}
              className="grid place-items-center w-7 h-7 rounded-lg transition-colors duration-150 hover:bg-[var(--bg-elevated)]"
              title={copied ? "Copied!" : "Copy"}
              aria-label="Copy message"
            >
              {copied ? <Check size={12} /> : <Copy size={12} />}
            </button>
          </div>
        </div>
      </div>
    );
  }

  /* ── Assistant message ── */
  return (
    <div className="flex gap-3 animate-fade-in">
      {/* Avatar */}
      <div className="shrink-0 mt-0.5">
        <SmaraLogo size={24} />
      </div>

      {/* Content */}
      <div className="flex-1 min-w-0 flex flex-col gap-2">
        <div
          className="rounded-2xl px-4 py-3.5 text-sm leading-7 shadow-sm"
          style={{
            color: "var(--text-primary)",
            background: "linear-gradient(135deg, color-mix(in srgb, var(--bg-surface) 92%, var(--accent) 8%), var(--bg-surface))",
            border: "1px solid var(--border-default)",
            boxShadow: "0 8px 24px rgba(0,0,0,0.10)",
          }}
        >
          <Markdown>{message.content}</Markdown>
        </div>

        {message.skillProposal && (
          <div
            className="self-start max-w-xl rounded-xl px-3.5 py-3"
            style={{
              background: "var(--accent-soft)",
              border: "1px solid var(--border-accent)",
            }}
          >
            <div className="flex items-start gap-2.5">
              <Sparkles size={15} className="mt-0.5 shrink-0" style={{ color: "var(--accent)" }} />
              <div className="min-w-0">
                <div className="text-[13px] font-semibold" style={{ color: "var(--text-primary)" }}>
                  {message.skillProposal.status === "approved"
                    ? "Smara learned a reusable workflow"
                    : "Smara noticed a reusable workflow"}
                </div>
                <div className="mt-1 text-[12px]" style={{ color: "var(--text-secondary)" }}>
                  {message.skillProposal.name}
                </div>
                {message.skillProposal.tools.length > 0 && (
                  <div className="mt-1.5 text-[11px]" style={{ color: "var(--text-muted)" }}>
                    {message.skillProposal.tools.map((tool) => tool.replace(/_/g, " ")).join(" → ")}
                  </div>
                )}
                {message.skillProposal.status === "pending" ? (
                  <div className="mt-2.5 flex gap-2">
                    <button
                      onClick={() => void respondSkillProposal(message.skillProposal!.id, true)}
                      className="rounded-lg px-3 py-1.5 text-[12px] font-semibold"
                      style={{ background: "var(--accent)", color: "var(--bg-base)" }}
                    >
                      Activate workflow
                    </button>
                    <button
                      onClick={() => void respondSkillProposal(message.skillProposal!.id, false)}
                      className="rounded-lg px-3 py-1.5 text-[12px]"
                      style={{
                        border: "1px solid var(--border-dim)",
                        color: "var(--text-secondary)",
                      }}
                    >
                      Not now
                    </button>
                  </div>
                ) : (
                  <div className="mt-2 text-[11px] font-medium" style={{ color: "var(--accent)" }}>
                    {message.skillProposal.status === "approved"
                      ? "Workflow activated"
                      : "Workflow not activated"}
                  </div>
                )}
              </div>
            </div>
          </div>
        )}

        {/* Copy button — always visible (dim), brightens on hover. No Edit
            affordance on assistant messages (the model wrote it; can't edit
            history). */}
        {message.content && (
          <div
            className="flex items-center gap-1 -mt-1 opacity-60 hover:opacity-100 transition-opacity duration-150"
            style={{ color: "var(--text-muted)" }}
          >
            <button
              onClick={handleCopy}
              className="grid place-items-center w-7 h-7 rounded-lg transition-colors duration-150 hover:bg-[var(--bg-elevated)]"
              title={copied ? "Copied!" : "Copy reply"}
              aria-label="Copy reply"
            >
              {copied ? <Check size={12} /> : <Copy size={12} />}
            </button>
          </div>
        )}

        {/* Download / preview — set by tools that produced a file (e.g. generate_pdf).
            PDFs open in the right-side preview panel; anything else (e.g.
            flashcards CSV) keeps the plain download link — browsers can't
            render a CSV usefully inline. */}
        {message.download && message.download.kind === "document" && (
          <div className="self-start inline-flex items-center rounded-xl overflow-hidden" style={{ border: "1px solid var(--border-accent)" }}>
            <button
              onClick={() => usePdfPreviewStore.getState().show(message.download!.url, message.download!.filename)}
              className="inline-flex items-center gap-2 pl-3 pr-2.5 py-2 text-[13px] font-medium transition-all duration-150"
              style={{ background: "var(--accent-soft)", color: "var(--accent)" }}
              onMouseEnter={(e) => { (e.currentTarget as HTMLButtonElement).style.background = "color-mix(in srgb, var(--accent) 18%, transparent)"; }}
              onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.background = "var(--accent-soft)"; }}
              title={`Preview ${message.download.filename}`}
            >
              <FileText size={14} />
              <span className="max-w-[220px] truncate">{message.download.filename}</span>
              <Eye size={13} />
            </button>
            <a
              href={`${message.download.url}${message.download.url.includes("?") ? "&" : "?"}dl=1`}
              download={message.download.filename}
              className="inline-flex items-center justify-center px-2.5 py-2 self-stretch transition-all duration-150"
              style={{ background: "var(--accent-soft)", color: "var(--accent)", borderLeft: "1px solid var(--border-accent)", textDecoration: "none" }}
              onMouseEnter={(e) => { (e.currentTarget as HTMLAnchorElement).style.background = "color-mix(in srgb, var(--accent) 18%, transparent)"; }}
              onMouseLeave={(e) => { (e.currentTarget as HTMLAnchorElement).style.background = "var(--accent-soft)"; }}
              title="Download"
              aria-label="Download"
            >
              <Download size={13} />
            </a>
          </div>
        )}
        {message.download && message.download.kind !== "document" && (
          <a
            href={message.download.url}
            download={message.download.filename}
            target="_blank"
            rel="noopener noreferrer"
            className="self-start inline-flex items-center gap-2 px-3 py-2 rounded-xl text-[13px] font-medium transition-all duration-150"
            style={{
              background: "var(--accent-soft)",
              border: "1px solid var(--border-accent)",
              color: "var(--accent)",
              textDecoration: "none",
            }}
            onMouseEnter={(e) => {
              const a = e.currentTarget as HTMLAnchorElement;
              a.style.background = "color-mix(in srgb, var(--accent) 18%, transparent)";
            }}
            onMouseLeave={(e) => {
              const a = e.currentTarget as HTMLAnchorElement;
              a.style.background = "var(--accent-soft)";
            }}
            title={message.download.filename}
          >
            <FileText size={14} />
            <span className="max-w-[260px] truncate">{message.download.filename}</span>
            <Download size={13} />
          </a>
        )}

        {/* Inline diagram — set by create_diagram (Mermaid rendered client-side). */}
        {message.diagram && <MermaidDiagram diagram={message.diagram} onNodeAsk={onNodeAsk} />}

        {/* Sources — the trust handle. Lists every external citation the
            tools returned this turn so the user can verify any claim against
            a real source. Hidden completely when no tool returned a URL. */}
        {message.sources && message.sources.length > 0 && (
          <div
            className="self-start max-w-full rounded-xl px-3 py-2"
            style={{
              background: "var(--bg-card)",
              border: "1px solid var(--border-dim)",
            }}
          >
            <div
              className="flex items-center gap-1.5 mb-1.5 text-[10px] uppercase tracking-wider font-semibold"
              style={{ color: "var(--text-muted)" }}
            >
              <Link2 size={10} />
              Sources ({message.sources.length})
            </div>
            <ol className="flex flex-col gap-1 m-0 p-0 list-none">
              {message.sources.map((c, i) => {
                const { label, href } = citationLabel(c);
                if (!label) return null;
                return (
                  <li key={i} className="flex items-start gap-1.5 text-[12px]">
                    <span
                      className="shrink-0 mt-[1px] inline-flex items-center justify-center min-w-[14px] h-[14px] px-1 rounded-full text-[9px] font-bold"
                      style={{
                        background: "var(--accent-soft)",
                        color: "var(--accent)",
                      }}
                    >
                      {i + 1}
                    </span>
                    {href ? (
                      <a
                        href={href}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="truncate inline-flex items-center gap-1"
                        style={{ color: "var(--accent)", textDecoration: "none" }}
                        title={href}
                      >
                        <span className="truncate">{label}</span>
                        <ExternalLink size={10} className="shrink-0 opacity-70" />
                      </a>
                    ) : (
                      <span
                        className="truncate"
                        style={{ color: "var(--text-secondary)" }}
                        title={c}
                      >
                        {label}
                      </span>
                    )}
                  </li>
                );
              })}
            </ol>
          </div>
        )}

        {/* Retrieval trace pill */}
        {message.retrievalTrace && (
          <div>
            <button
              onClick={() => setShowTrace((v) => !v)}
              className={clsx(
                "flex items-center gap-1.5 text-[11px] px-2.5 py-1 rounded-full transition-all duration-150",
                showTrace
                  ? "text-indigo-300"
                  : "text-[var(--text-dim)] hover:text-[var(--text-muted)]"
              )}
              style={{
                background: showTrace ? "rgba(129,140,248,0.10)" : "var(--bg-elevated)",
                border: `1px solid ${showTrace ? "rgba(129,140,248,0.20)" : "var(--border-dim)"}`,
              }}
            >
              <Brain size={10} />
              {showTrace
                ? "Hide retrieval"
                : `${message.retrievalTrace.memoriesUsed ?? 0} memor${
                    (message.retrievalTrace.memoriesUsed ?? 0) === 1 ? "y" : "ies"
                  } used · ${message.retrievalTrace.latencyMs}ms`}
              {showTrace ? <ChevronUp size={10} /> : <ChevronDown size={10} />}
            </button>
            {showTrace && (
              <div className="mt-1.5">
                <RetrievalPanel trace={message.retrievalTrace} />
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
