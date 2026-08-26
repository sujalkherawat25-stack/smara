import { useRef, useEffect, useState, useMemo, useCallback, KeyboardEvent } from "react";
import { useChatStore } from "@/stores/chatStore";
import MessageBubble from "./MessageBubble";
import StreamingMessage from "./StreamingMessage";
import AgentRunCanvas from "./AgentRunCanvas";
import SlashCommandPalette, { extractSlashQuery } from "./SlashCommandPalette";
import SmaraLogo from "@/components/SmaraLogo";
import SmaraVoice from "@/components/Voice/SmaraVoice";
import { useAuthStore } from "@/stores/authStore";
import { useConversationsStore } from "@/stores/conversationsStore";
import { apiClient, ApiError } from "@/api/client";
import { ArrowUp, Square, Network, Database, Paperclip, Camera, FileText, Image as ImageIcon, X, Loader2, Sparkles } from "lucide-react";
import { smaraModeEnabled } from "@/lib/smaraGateway";

interface Attachment {
  id: string;
  filename: string;
  kind: "document" | "image";
}

// Document types accepted for upload (matches backend convert.SUPPORTED_EXTENSIONS).
const DOC_EXTS = [".pdf", ".docx", ".xlsx", ".pptx", ".csv", ".txt", ".html", ".htm", ".json", ".xml", ".md"];
// J7: images go through the vision pre-pass. Free=2MB, Founder=4MB (enforced server-side).
const IMAGE_EXTS = [".jpg", ".jpeg", ".png", ".webp", ".gif"];
const ACCEPTED_EXTS = [...DOC_EXTS, ...IMAGE_EXTS];
const ACCEPT_ATTR = ACCEPTED_EXTS.join(",");

export default function ChatWindow() {
  const messages = useChatStore((s) => s.messages);
  const isStreaming = useChatStore((s) => s.isStreaming);
  const send = useChatStore((s) => s.send);
  const stop = useChatStore((s) => s.stop);
  const deepReasoning = useChatStore((s) => s.deepReasoning);
  const setDeepReasoning = useChatStore((s) => s.setDeepReasoning);
  const account = useAuthStore((s) => s.account);
  const setAccountScope = useConversationsStore((s) => s.setAccountScope);
  const hydrateFromServer = useConversationsStore((s) => s.hydrateFromServer);
  const focusedSmara = smaraModeEnabled();

  // Never reuse browser-local recents across accounts on a shared browser.
  useEffect(() => {
    setAccountScope(account?.account_id ?? null);
    if (focusedSmara && account?.account_id) {
      void hydrateFromServer().catch(() => {
        // Keep the local draft cache usable during a transient bridge or
        // deployment failure; the next app focus will reconcile it again.
      });
    }
  }, [account?.account_id, focusedSmara, hydrateFromServer, setAccountScope]);

  const bottomRef = useRef<HTMLDivElement>(null);
  const taRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const cameraInputRef = useRef<HTMLInputElement>(null);
  const [input, setInput] = useState("");
  const [focused, setFocused] = useState(false);
  // Slash-command palette: open when input starts with "/" and the user
  // hasn't typed a space yet (free-form message). Auto-dismissed once
  // the input shape stops matching, or via Esc.
  const [slashDismissed, setSlashDismissed] = useState(false);
  const slashOpen = !slashDismissed && extractSlashQuery(input) !== null;
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [uploading, setUploading] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isStreaming]);

  useEffect(() => {
    const el = taRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 200) + "px";
  }, [input]);

  const uploadFiles = async (files: FileList | File[]) => {
    if (focusedSmara) {
      setUploadError("File attachments are not enabled in the focused hosted release yet.");
      return;
    }
    setUploadError(null);
    const list = Array.from(files);
    for (const file of list) {
      const lower = file.name.toLowerCase();
      const isImage = IMAGE_EXTS.some((ext) => lower.endsWith(ext));
      if (!isImage && !DOC_EXTS.some((ext) => lower.endsWith(ext))) {
        setUploadError("Unsupported file. Use an image (JPG/PNG/WebP), PDF, Word, Excel, PowerPoint, CSV, or text.");
        continue;
      }
      if (isImage && file.size > 4 * 1024 * 1024) {
        setUploadError(`"${file.name}" is over 4 MB (image limit for vision models).`);
        continue;
      }
      if (!isImage && file.size > 40 * 1024 * 1024) {
        setUploadError(`"${file.name}" is over 40 MB.`);
        continue;
      }
      setUploading(true);
      try {
        const res = await apiClient.uploadFile<{ attachment_id: string; filename: string; kind?: string }>(
          "/v1/memento/upload",
          file,
        );
        setAttachments((a) => [...a, {
          id: res.attachment_id,
          filename: res.filename,
          kind: res.kind === "image" ? "image" : "document",
        }]);
      } catch (e) {
        setUploadError(e instanceof ApiError ? e.detail : `Couldn't upload "${file.name}".`);
      } finally {
        setUploading(false);
      }
    }
  };

  const removeAttachment = (id: string) => setAttachments((a) => a.filter((x) => x.id !== id));

  const handleSend = () => {
    const text = input.trim();
    if (isStreaming) return;
    if (!text && attachments.length === 0) return;
    const ids = attachments.map((a) => a.id);
    // If a file is attached with no typed message, give the agent a default ask.
    const allImages = attachments.length > 0 && attachments.every((a) => a.kind === "image");
    const message = text || (allImages
      ? "What do you see in the attached image(s)?"
      : "Please read and summarise the attached document.");
    setInput("");
    setAttachments([]);
    setUploadError(null);
    send(message, ids);
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  // Keep the message tree out of the keystroke render path.  ChatWindow owns
  // the composer draft, so it renders on every keypress; rebuilding every
  // markdown/diagram bubble here made the input feel laggy on long chats.
  // These callbacks and elements stay stable until messages actually change.
  const handleNodeAsk = useCallback((prompt: string) => {
    setInput(prompt);
    requestAnimationFrame(() => taRef.current?.focus());
  }, []);

  const handleEdit = useCallback((text: string) => {
    setInput(text);
    requestAnimationFrame(() => {
      const ta = taRef.current;
      if (ta) {
        ta.focus();
        ta.setSelectionRange(ta.value.length, ta.value.length);
      }
    });
  }, []);

  const renderedMessages = useMemo(() => messages.map((msg) => (
    <MessageBubble
      key={msg.id}
      message={msg}
      onNodeAsk={handleNodeAsk}
      onEdit={handleEdit}
    />
  )), [messages, handleNodeAsk, handleEdit]);

  const empty = messages.length === 0 && !isStreaming;
  const canSend = !isStreaming && (!!input.trim() || attachments.length > 0);

  return (
    <div className="flex flex-col h-full" style={{ background: "var(--bg-base)" }}>

      {/* ── Messages ─────────────────────────────────────────────────── */}
      <div className="flex-1 overflow-y-auto scrollbar-thin">
        <div className="mx-auto w-full max-w-2xl px-4 py-8 space-y-6">

          {empty && <EmptyState onPick={(p) => { void send(p); }} />}

          {renderedMessages}
          {isStreaming && <AgentRunCanvas />}
          {isStreaming && <StreamingMessage />}
          <div ref={bottomRef} />
        </div>
      </div>

      {/* ── Composer ─────────────────────────────────────────────────── */}
      <div
        className="shrink-0 px-4 pt-3 pb-4 relative"
        style={{ borderTop: "1px solid var(--border-dim)" }}
        onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
        onDragLeave={(e) => { e.preventDefault(); setDragOver(false); }}
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          if (e.dataTransfer.files?.length) uploadFiles(e.dataTransfer.files);
        }}
      >
        {/* Drag overlay */}
        {dragOver && (
          <div
            className="absolute inset-0 z-10 m-2 rounded-2xl flex items-center justify-center pointer-events-none"
            style={{
              background: "var(--accent-soft)",
              border: "2px dashed var(--accent)",
              color: "var(--accent)",
            }}
          >
            <div className="flex items-center gap-2 text-sm font-medium">
              <FileText size={16} /> Drop a document or image to attach
            </div>
          </div>
        )}

        <div className="mx-auto w-full max-w-2xl">
          {/* Hidden file input (browse) */}
          <input
            ref={fileInputRef}
            type="file"
            accept={ACCEPT_ATTR}
            multiple
            className="hidden"
            onChange={(e) => { if (e.target.files?.length) uploadFiles(e.target.files); e.target.value = ""; }}
          />

          {/* Hidden camera input — `capture` opens the rear camera on mobile
              (Chrome / Safari on iOS+Android). On desktop browsers without
              camera-capture support (Firefox, sometimes Safari) it gracefully
              falls back to a normal image-only file picker. Same uploadFiles
              path so size cap, kind detection, etc. all just work. */}
          <input
            ref={cameraInputRef}
            type="file"
            accept="image/*"
            capture="environment"
            className="hidden"
            onChange={(e) => { if (e.target.files?.length) uploadFiles(e.target.files); e.target.value = ""; }}
          />

          {/* Attachment chips */}
          {(attachments.length > 0 || uploading || uploadError) && (
            <div className="flex flex-wrap items-center gap-2 mb-2">
              {attachments.map((a) => (
                <div
                  key={a.id}
                  className="flex items-center gap-1.5 pl-2 pr-1 py-1 rounded-lg text-xs"
                  style={{ background: "var(--bg-elevated)", border: "1px solid var(--border-default)" }}
                >
                  {a.kind === "image"
                    ? <ImageIcon size={12} style={{ color: "var(--accent2)" }} />
                    : <FileText size={12} style={{ color: "var(--accent)" }} />}
                  <span style={{ color: "var(--text-secondary)" }} className="max-w-[200px] truncate">
                    {a.filename}
                  </span>
                  <button
                    onClick={() => removeAttachment(a.id)}
                    className="p-0.5 rounded hover:opacity-100 opacity-60"
                    style={{ color: "var(--text-muted)" }}
                    title="Remove"
                  >
                    <X size={12} />
                  </button>
                </div>
              ))}
              {uploading && (
                <div className="flex items-center gap-1.5 text-xs" style={{ color: "var(--text-muted)" }}>
                  <Loader2 size={12} className="animate-spin" /> Uploading…
                </div>
              )}
              {uploadError && (
                <span className="text-xs" style={{ color: "#ef4444" }}>{uploadError}</span>
              )}
            </div>
          )}

          {/* Input box (relative wrapper so SlashCommandPalette can anchor) */}
          <div className="relative">
            {slashOpen && (
              <SlashCommandPalette
                value={input}
                onCommit={(newValue, caretAt) => {
                  setInput(newValue);
                  setSlashDismissed(true);
                  requestAnimationFrame(() => {
                    const ta = taRef.current;
                    if (ta) {
                      ta.focus();
                      ta.setSelectionRange(caretAt, caretAt);
                    }
                  });
                }}
                onDismiss={() => setSlashDismissed(true)}
              />
            )}
          <div
            className="chat-composer flex items-end gap-2 rounded-2xl px-3 py-3 transition-all duration-200"
            style={{
              background: "var(--bg-surface)",
              border: focused
                ? "1px solid var(--border-accent)"
                : "1px solid var(--border-default)",
              boxShadow: focused
                ? "0 0 0 3px var(--accent-dim), var(--shadow-sm)"
                : "var(--shadow-sm)",
            }}
          >
            {!focusedSmara && <>
              {/* Attach (browse) button */}
              <button
                onClick={() => fileInputRef.current?.click()}
                disabled={isStreaming}
                className="shrink-0 grid place-items-center w-8 h-8 rounded-xl transition-colors duration-150"
                style={{ color: "var(--text-muted)", opacity: isStreaming ? 0.4 : 1 }}
                title="Attach a document or image (PDF, Word, Excel, PPT, CSV, JPG, PNG)"
                onMouseEnter={(e) => { (e.currentTarget as HTMLButtonElement).style.background = "var(--bg-elevated)"; }}
                onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.background = "transparent"; }}
              >
                <Paperclip size={15} />
              </button>

              {/* Camera button — opens phone camera on mobile, image picker on desktop */}
              <button
                onClick={() => cameraInputRef.current?.click()}
                disabled={isStreaming}
                className="shrink-0 grid place-items-center w-8 h-8 rounded-xl transition-colors duration-150"
                style={{ color: "var(--text-muted)", opacity: isStreaming ? 0.4 : 1 }}
                title="Take a photo with the camera (mobile) or pick an image (desktop)"
                onMouseEnter={(e) => { (e.currentTarget as HTMLButtonElement).style.background = "var(--bg-elevated)"; }}
                onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.background = "transparent"; }}
              >
                <Camera size={15} />
              </button>

              {/* Real-time, interruptible speech-to-speech conversation. */}
              <SmaraVoice />
            </>}

            <textarea
              ref={taRef}
              rows={1}
              className="flex-1 bg-transparent resize-none outline-none text-sm leading-6 py-0.5 max-h-[200px] scrollbar-thin"
              style={{ color: "var(--text-primary)" }}
              placeholder="Message Smara…"
              value={input}
              onChange={(e) => {
                setInput(e.target.value);
                // If input no longer starts with "/", the palette will close
                // automatically. If user re-enters with "/", clear the
                // previously-dismissed flag so the palette can reopen.
                if (!e.target.value.startsWith("/")) setSlashDismissed(false);
              }}
              onKeyDown={handleKeyDown}
              onFocus={() => setFocused(true)}
              onBlur={() => setFocused(false)}
              onPaste={(e) => {
                const files = Array.from(e.clipboardData.files).filter((f) =>
                  f.type.startsWith("image/")
                );
                if (files.length) { e.preventDefault(); uploadFiles(files); }
              }}
            />

            {/* Send / stop button */}
            <button
              onClick={isStreaming ? stop : handleSend}
              disabled={!isStreaming && !canSend}
              className="shrink-0 grid place-items-center w-8 h-8 rounded-xl transition-all duration-150"
              style={{
                background: isStreaming
                  ? "var(--bg-elevated)"
                  : canSend
                  ? "linear-gradient(135deg, var(--accent) 0%, var(--accent2) 100%)"
                  : "var(--bg-elevated)",
                color: isStreaming ? "var(--text-secondary)" : canSend ? "#fff" : "var(--text-dim)",
                boxShadow: canSend && !isStreaming ? "0 2px 12px rgba(201,169,110,0.32)" : "none",
                opacity: !isStreaming && !canSend ? 0.5 : 1,
                border: isStreaming ? "1px solid var(--border-default)" : "none",
              }}
              title={isStreaming ? "Stop (cancel response)" : "Send (Enter)"}
            >
              {isStreaming ? <Square size={13} fill="currentColor" /> : <ArrowUp size={14} />}
            </button>
          </div>
          </div>{/* close relative wrapper around input + palette */}

          {/* In-project scope chip — every message sent here is scoped to
              this Project's documents (search_documents) until you leave. */}
          {/* Toolbar */}
          <div className="flex items-center justify-between mt-2.5 px-1">
            <div className="flex items-center gap-1.5">
              {!focusedSmara && <>
                <ToolbarBtn
                  active={false}
                  onClick={() => undefined}
                  icon={<Network size={12} />}
                  label="Knowledge"
                  accentColor="var(--accent)"
                />
                <ToolbarBtn
                  active={false}
                  onClick={() => undefined}
                  icon={<Database size={12} />}
                  label="Memory"
                  accentColor="var(--accent2)"
                />
              </>}
              <ToolbarBtn
                active={deepReasoning}
                onClick={() => setDeepReasoning(!deepReasoning)}
                icon={<Sparkles size={12} />}
                label={deepReasoning ? "Deep reasoning on" : "Deep reasoning"}
                accentColor="#e8a86b"
              />
            </div>
            <div className="hidden sm:flex items-center gap-1.5 text-[10px] select-none" style={{ color: "var(--text-dim)" }}>
              <kbd className="keycap">↵</kbd><span>send</span>
              <span style={{ color: "var(--border-default)" }}>·</span>
              <kbd className="keycap">⇧↵</kbd><span>newline</span>
            </div>
          </div>

          {/* AI-output disclaimer — same convention every modern chat app uses */}
          <p
            className="text-center text-[10px] mt-1.5 select-none"
            style={{ color: "var(--text-dim)" }}
          >
            Smara is AI and can make mistakes. Please double-check responses.
          </p>
        </div>
      </div>
    </div>
  );
}

/**
 * EmptyState — what the user sees on a fresh chat with no messages.
 *
 * Unified UX across mobile and desktop:
 *   • Soft halo + animated logo
 *   • Time-of-day greeting personalised with the user's first name
 *   • Single tagline below
 *   • Nothing else — no tool chip showcase, no suggested prompts
 *
 * Why: capability/suggestion chips made the surface feel like a product demo
 * page, not a conversation. Removing them mirrors Claude/ChatGPT/Gemini's
 * mobile aesthetic and pushes users to type / use voice / send a file —
 * the actual jobs Smara is built for.
 *
 * On first sign-in, this becomes a guided welcome that gives Smara useful
 * context before the first conversation begins.
 */
function EmptyState({ onPick }: { onPick: (prompt: string) => void }) {
  const account = useAuthStore((s) => s.account);
  // Greeting cascade — match the server-side _resolve_self_name logic
  // (system_prompt.py) so the empty-state greeting and Smara's actual
  // replies use the SAME name:
  //   1. preferred_name (set by user via onboarding or "call me X")
  //   2. first token of display_name (from Google sign-in)
  //   3. "there" — generic fallback
  // When the user later renames themselves via "call me SK", the chat
  // store's auth refresh after that turn brings the new preferred_name
  // into this component, so the greeting updates without a reload.
  const preferred = (account?.preferred_name || "").trim();
  const firstName =
    preferred ||
    ((account?.display_name || "").trim().split(/\s+/)[0]) ||
    "there";

  // New users start with a guided welcome instead of a blank chat. Completing
  // it records the details through a normal first agent turn and marks the
  // account as onboarded, so this only appears once.
  // Setup is optional. New users land directly in chat; their first message
  // naturally establishes context without blocking the composer behind a form.
  const isFirstTime = false;

  // Time-of-day greeting respecting the user's local clock.
  const hour = new Date().getHours();
  const greeting =
    hour < 5  ? "Late night" :
    hour < 12 ? "Good morning" :
    hour < 17 ? "Afternoon" :
    hour < 21 ? "Evening" :
                "Late evening";

  if (isFirstTime) {
    return <OnboardingCard onPick={onPick} />;
  }

  return (
    <div className="flex flex-col items-center justify-center text-center pt-12 md:pt-20 select-none animate-fade-in">
      {/* Logo with soft halo */}
      <div className="relative mb-6 md:mb-8 flex items-center justify-center">
        <div
          className="absolute rounded-full orb-pulse pointer-events-none"
          style={{
            width: "180px",
            height: "180px",
            background: "radial-gradient(circle, var(--accent) 0%, transparent 65%)",
            opacity: 0.10,
            filter: "blur(16px)",
          }}
        />
        <SmaraLogo size={96} animate />
      </div>

      {/* Same greeting shape on every device — personal, warm, time-aware */}
      <h1
        className="font-display text-[26px] md:text-[30px] font-bold tracking-tight"
        style={{ color: "var(--text-primary)" }}
      >
        {greeting}, {firstName}
      </h1>

      {/* One-line tagline */}
      <p
        className="mt-2 text-[12px] md:text-[13px] tracking-wide"
        style={{ color: "var(--accent)" }}
      >
      </p>
    </div>
  );
}

function OnboardingCard({ onPick }: { onPick: (prompt: string) => void }) {
  const complete = useAuthStore((s) => s.completeOnboarding);
  const [name, setName] = useState("");
  const [focus, setFocus] = useState("Work");
  const [memory, setMemory] = useState("");
  const [weeklyPriority, setWeeklyPriority] = useState("");
  const [birthday, setBirthday] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const finish = async () => {
    setSaving(true); setError("");
    try {
      await complete(name.trim() || null, {
        birthday: birthday || undefined,
      });
      const facts = [
        `My main focus right now is ${focus}.`,
        memory.trim() && `Please remember this about me: ${memory.trim()}`,
        weeklyPriority.trim() && `One thing I need done this week: ${weeklyPriority.trim()}`,
        birthday && `My birthday is ${birthday}.`,
      ].filter(Boolean).join("\n");
      onPick(`${facts}\n\nHelp me turn this into a simple plan and remember what will help next time.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't save your welcome details.");
      setSaving(false);
    }
  };

  const skip = async () => {
    setSaving(true); setError("");
    try {
      // Mark the one-time welcome as dismissed without recording any of the
      // form fields. This immediately unlocks the normal chat composer.
      await complete(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't skip the welcome right now.");
      setSaving(false);
    }
  };

  return (
    <div className="flex flex-col items-center justify-center pt-6 md:pt-10 px-4 animate-fade-in">
      <div className="relative mb-4"><SmaraLogo size={58} animate /></div>
      <div className="relative w-full max-w-[620px] rounded-2xl p-5 md:p-6" style={{ background: "var(--bg-surface)", border: "1px solid var(--border-default)", boxShadow: "0 12px 40px rgba(0,0,0,.18)" }}>
        <button
          type="button"
          onClick={() => void skip()}
          disabled={saving}
          aria-label="Skip setup and open chat"
          title="Skip setup"
          className="absolute right-4 top-4 grid h-8 w-8 place-items-center rounded-lg transition-colors hover:bg-white/5 disabled:opacity-50"
          style={{ color: "var(--text-muted)" }}
        >
          <X size={16} />
        </button>
        <p className="text-[11px] font-semibold tracking-[.16em] uppercase" style={{ color: "var(--accent)" }}>Meet Smara</p>
        <h1 className="font-display text-[24px] md:text-[28px] font-bold mt-2" style={{ color: "var(--text-primary)" }}>Let&apos;s make this useful from day one.</h1>
        <p className="text-[13px] mt-2" style={{ color: "var(--text-muted)" }}>A few details help Smara remember what matters and follow through with you.</p>
        <div className="grid md:grid-cols-2 gap-4 mt-5">
          <label className="text-[12px] font-medium" style={{ color: "var(--text-secondary)" }}>What should Smara call you?
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Your name" className="mt-1.5 w-full rounded-xl px-3 py-2.5 text-[13px] outline-none" style={{ background: "var(--bg-elevated)", border: "1px solid var(--border-default)", color: "var(--text-primary)" }} />
          </label>
          <label className="text-[12px] font-medium" style={{ color: "var(--text-secondary)" }}>What should Smara help you stay on top of?
            <select value={focus} onChange={(e) => setFocus(e.target.value)} className="mt-1.5 w-full rounded-xl px-3 py-2.5 text-[13px] outline-none" style={{ background: "var(--bg-elevated)", border: "1px solid var(--border-default)", color: "var(--text-primary)" }}>
              <option>Study</option><option>Work</option><option>Business / freelancing</option><option>Personal life</option>
            </select>
          </label>
        </div>
        <label className="block text-[12px] font-medium mt-4" style={{ color: "var(--text-secondary)" }}>What should Smara remember about you?
          <textarea value={memory} onChange={(e) => setMemory(e.target.value)} placeholder="Your goals, preferences, people, routines, or anything that helps Smara support you better." rows={2} className="mt-1.5 w-full rounded-xl px-3 py-2.5 text-[13px] outline-none resize-none" style={{ background: "var(--bg-elevated)", border: "1px solid var(--border-default)", color: "var(--text-primary)" }} />
        </label>
        <label className="block text-[12px] font-medium mt-4" style={{ color: "var(--text-secondary)" }}>What is one thing you need done this week?
          <input value={weeklyPriority} onChange={(e) => setWeeklyPriority(e.target.value)} placeholder="For example: finish my proposal, revise calculus, or call three clients." className="mt-1.5 w-full rounded-xl px-3 py-2.5 text-[13px] outline-none" style={{ background: "var(--bg-elevated)", border: "1px solid var(--border-default)", color: "var(--text-primary)" }} />
        </label>
        <div className="mt-4 p-3 rounded-xl" style={{ background: "var(--accent-soft)", border: "1px solid var(--border-default)" }}>
          <label className="text-[12px] font-medium" style={{ color: "var(--text-secondary)" }}>Your birthday
            <input type="date" value={birthday} onChange={(e) => setBirthday(e.target.value)} className="mt-1.5 block rounded-xl px-3 py-2 text-[13px] outline-none" style={{ background: "var(--bg-elevated)", border: "1px solid var(--border-default)", color: "var(--text-primary)" }} />
          </label>
        </div>
        {error && <p className="mt-3 text-[12px]" style={{ color: "var(--danger)" }}>{error}</p>}
        <button onClick={() => void finish()} disabled={saving} className="mt-5 w-full rounded-xl py-3 text-[13px] font-bold disabled:opacity-60" style={{ background: "linear-gradient(135deg, var(--accent), var(--accent2))", color: "#fff" }}>{saving ? "Setting up Smara…" : "Start with Smara →"}</button>
        <p className="text-center text-[10px] mt-3" style={{ color: "var(--text-dim)" }}>You can edit or remove anything Smara remembers at any time.</p>
      </div>
    </div>
  );
}


function ToolbarBtn({
  active, onClick, icon, label, accentColor,
}: {
  active: boolean;
  onClick: () => void;
  icon: React.ReactNode;
  label: string;
  accentColor?: string;
}) {
  const accent = accentColor ?? "var(--accent)";
  return (
    <button
      onClick={onClick}
      className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-[12px] transition-all duration-150"
      style={
        active
          ? {
              background: `color-mix(in srgb, ${accent} 10%, transparent)`,
              color: accent,
              border: `1px solid color-mix(in srgb, ${accent} 25%, transparent)`,
            }
          : {
              background: "transparent",
              color: "var(--text-muted)",
              border: "1px solid var(--border-dim)",
            }
      }
      onMouseEnter={(e) => {
        if (!active) {
          const b = e.currentTarget as HTMLButtonElement;
          b.style.background = "var(--bg-elevated)";
          b.style.borderColor = "var(--border-default)";
          b.style.color = "var(--text-secondary)";
        }
      }}
      onMouseLeave={(e) => {
        if (!active) {
          const b = e.currentTarget as HTMLButtonElement;
          b.style.background = "transparent";
          b.style.borderColor = "var(--border-dim)";
          b.style.color = "var(--text-muted)";
        }
      }}
    >
      {icon}
      {label}
    </button>
  );
}
