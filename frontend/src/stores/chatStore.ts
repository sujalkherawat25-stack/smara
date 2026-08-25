import { create } from "zustand";
import type {
  ConversationMessage,
  MessageDiagram,
  MessageDownload,
  MessageSkillProposal,
  RetrievalTrace,
} from "@/types/memory";
import { useConversationsStore } from "./conversationsStore";
import { useNotificationsStore } from "./notificationsStore";
import { useMemoryStore } from "./memoryStore";
import { useProjectsStore } from "./projectsStore";
import { apiClient, ApiError } from "@/api/client";
import { isMaintenanceError, MAINTENANCE_MESSAGE } from "@/lib/httpErrors";
import { smaraFetch, smaraModeEnabled, smaraStream } from "@/lib/smaraGateway";

// Tool names whose successful execution mutates user memory. When any
// of these report ok=true over the SSE stream, bump memoryStore.dirtyTick
// so the Memory panel refetches from /v1/memory and stays in sync.
const _MEMORY_MUTATING_TOOLS = new Set([
  "save_memory",
  "update_memory",
  "forget_memory",
]);

const BASE_URL = ""; // relative — routed through Vite proxy to backend
// F3: cookie auth via credentials: "include" — no X-User-ID header anymore.

// ── Activity feed types (matches app/memento/events.py wire schema) ──────────

export type AgentPhase = "triage" | "retrieve" | "reason_act" | "answer";

export interface ActivityThought {
  kind: "thought";
  text: string;
}
export interface ActivityMemorySearch {
  kind: "memory_search";
  query: string;
  hits: number;
}
export interface ActivityToolCall {
  kind: "tool_call";
  name: string;
  args: Record<string, unknown>;
}
export interface ActivityToolResult {
  kind: "tool_result";
  name: string;
  ok: boolean;
  preview: string;
  citations?: string[];
}
export interface ActivityError {
  kind: "error";
  message: string;
  recoverable: boolean;
}
export interface ActivityStatus {
  kind: "status";
  label: string;
  detail?: string;
  progress?: number;
}
export interface ActivitySkillProposal extends MessageSkillProposal {
  kind: "skill_proposal";
}
// J1: the agent paused on a risk="confirm" tool and is waiting for the user.
export interface ActivityConfirmRequest {
  kind: "confirm_request";
  id: string;
  name: string;
  preview: string;
  status: "pending" | "approved" | "denied" | "timeout";
}

export type ActivityItem =
  | ActivityThought
  | ActivityMemorySearch
  | ActivityToolCall
  | ActivityToolResult
  | ActivityStatus
  | ActivitySkillProposal
  | ActivityError
  | ActivityConfirmRequest;

// ── Store shape ──────────────────────────────────────────────────────────────

interface ChatState {
  // Active session
  sessionId: string;          // maps to Conversation.id in conversationsStore
  conversationId: string;     // backend conversation_id
  messages: ConversationMessage[];

  // Streaming state for the currently-in-flight turn
  isStreaming: boolean;
  streamingText: string;
  currentPhase: AgentPhase | null;
  activity: ActivityItem[];   // live feed of reasoning + tool events
  deepReasoning: boolean;
  reasoningModel: "kimi";
  setReasoningModel: (model: "kimi") => void;
  setDeepReasoning: (enabled: boolean) => void;

  // Abort handle for the in-flight fetch — null when not streaming
  _abortController: AbortController | null;

  send: (text: string, attachmentIds?: string[]) => Promise<void>;
  stop: () => void;

  // J1: answer a pending tool confirmation (Approve/Deny buttons)
  respondConfirmation: (confirmId: string, approved: boolean) => Promise<void>;
  respondSkillProposal: (skillId: string, approved: boolean) => Promise<void>;

  // Start a brand-new empty chat
  newChat: () => void;

  // Load an existing conversation from store
  loadConversation: (sessionId: string, conversationId: string, messages: ConversationMessage[]) => void;
}

function freshSession() {
  return {
    sessionId: crypto.randomUUID(),
    conversationId: crypto.randomUUID(),
    messages: [] as ConversationMessage[],
  };
}

export const useChatStore = create<ChatState>((set, get) => ({
  ...freshSession(),
  isStreaming: false,
  streamingText: "",
  currentPhase: null,
  activity: [],
  deepReasoning: false,
  reasoningModel: "kimi",
  setReasoningModel: (model) => set({ reasoningModel: model, deepReasoning: true }),
  setDeepReasoning: (enabled) => set({ deepReasoning: enabled }),
  _abortController: null,

  stop: () => {
    const { _abortController } = get();
    if (_abortController) _abortController.abort();
    // State cleanup happens in send()'s catch block when AbortError fires.
  },

  respondConfirmation: async (confirmId: string, approved: boolean) => {
    // Optimistic flip so the buttons disable instantly; the stream's
    // confirm_resolved event is the authoritative confirmation.
    set((s) => ({
      activity: s.activity.map((a) =>
        a.kind === "confirm_request" && a.id === confirmId && a.status === "pending"
          ? { ...a, status: approved ? "approved" : "denied" }
          : a
      ),
    }));
    try {
      if (smaraModeEnabled()) {
        // Smara's durable task approvals are rendered by the Tasks surface;
        // chat confirmations are a Memento-only event until that surface is
        // migrated. Do not guess a task id from a chat confirmation id.
        throw new Error("Chat confirmations are not available in Smara bridge mode yet.");
      }
      const res = await smaraFetch(`${BASE_URL}/v1/memento/confirm/${confirmId}`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ approved }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
    } catch (err) {
      console.error("Confirmation response failed:", err);
      useNotificationsStore.getState().push({
        kind: "error",
        title: "Couldn't send your decision",
        body: "The confirmation may have expired — the action will be skipped.",
        ttl_ms: 6000,
      });
    }
  },

  respondSkillProposal: async (skillId: string, approved: boolean) => {
    const nextStatus = approved ? "approved" : "disabled";
    set((s) => ({
      activity: s.activity.map((a) =>
        a.kind === "skill_proposal" && a.id === skillId
          ? { ...a, status: nextStatus }
          : a
      ),
      messages: s.messages.map((message) =>
        message.skillProposal?.id === skillId
          ? {
              ...message,
              skillProposal: { ...message.skillProposal, status: nextStatus },
            }
          : message
      ),
    }));
    {
      const snapshot = get();
      useConversationsStore.getState().save(
        snapshot.sessionId,
        snapshot.messages,
        snapshot.conversationId,
      );
    }
    try {
      const action = approved ? "approve" : "disable";
      const res = await smaraFetch(`${BASE_URL}/v1/memento/skills/${skillId}/${action}`, {
        method: "POST",
        credentials: "include",
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
    } catch (err) {
      console.error("Skill decision failed:", err);
      useNotificationsStore.getState().push({
        kind: "error",
        title: "Couldn't update that workflow",
        body: "Please try the approval again.",
        ttl_ms: 6000,
      });
      set((s) => ({
        activity: s.activity.map((a) =>
          a.kind === "skill_proposal" && a.id === skillId
            ? { ...a, status: "pending" }
            : a
        ),
        messages: s.messages.map((message) =>
          message.skillProposal?.id === skillId
            ? {
                ...message,
                skillProposal: { ...message.skillProposal, status: "pending" },
              }
            : message
        ),
      }));
      const snapshot = get();
      useConversationsStore.getState().save(
        snapshot.sessionId,
        snapshot.messages,
        snapshot.conversationId,
      );
    }
  },

  send: async (text: string, attachmentIds: string[] = []) => {
    const { conversationId, sessionId } = get();

    // ── /feedback intercept ─────────────────────────────────────────────
    // Mirror Telegram's behaviour: a message starting with `/feedback `
    // routes to the dedicated feedback API — NOT through the agent loop.
    // Saves tokens, gives instant confirmation, and ensures the note
    // actually lands in the Postgres feedback table (the agent can't
    // currently write there). Bare `/feedback` (no body) shows usage.
    const fbMatch = text.match(/^\/feedback\b\s*(.*)$/is);
    if (fbMatch) {
      const note = fbMatch[1].trim();
      const userMsg: ConversationMessage = {
        id: crypto.randomUUID(),
        role: "user",
        content: text,
        timestamp: new Date().toISOString(),
      };
      const assistantId = crypto.randomUUID();
      set((s) => ({
        messages: [...s.messages, userMsg],
        isStreaming: false,
      }));

      let replyContent: string;
      if (!note) {
        replyContent =
          "To send feedback, type it inline after the command:\n\n" +
          "    /feedback the PDF export was confusing\n\n" +
          "Anything — bugs, ideas, what you'd love to see — goes straight to the founders.";
      } else if (note.length < 3) {
        replyContent = "Could you give me a few more words? Hard to act on something that short.";
      } else {
        try {
          await apiClient.post("/v1/memento/feedback", {
            message: note,
            channel: "web",
          });
          replyContent =
            "✅ Got it — your feedback just landed in the founders' inbox.\n\n" +
            "I read every single one. If it's a bug, I'll usually fix it within a day or two and DM you.";
        } catch (e) {
          let detail = "";
          if (e instanceof ApiError) {
            if (e.status === 401) detail = "Your session expired — refresh and sign in again, then resend.";
            else if (e.status === 503) detail = "Feedback service is briefly down — try again in a moment.";
            else if (e.status === 422) detail = e.detail || "Validation failed.";
            else detail = e.detail || `Couldn't save (${e.status}).`;
          } else {
            detail = "Network error — check your connection and try again.";
          }
          replyContent =
            "Couldn't save your feedback right now.\n\n" +
            detail +
            "\n\nYour note is still in the box above — just resend.";
        }
      }

      const assistantMsg: ConversationMessage = {
        id: assistantId,
        role: "assistant",
        content: replyContent,
        timestamp: new Date().toISOString(),
      };
      set((s) => {
        const next = [...s.messages, assistantMsg];
        useConversationsStore.getState().save(sessionId, next, conversationId);
        return { messages: next };
      });
      return;
    }

    const userMsg: ConversationMessage = {
      id: crypto.randomUUID(),
      role: "user",
      content: text,
      timestamp: new Date().toISOString(),
    };
    const assistantId = crypto.randomUUID();

    const abortController = new AbortController();

    set((s) => ({
      messages: [...s.messages, userMsg],
      isStreaming: true,
      streamingText: "",
      currentPhase: null,
      activity: [],
      _abortController: abortController,
    }));

    try {
      const smara = smaraModeEnabled();
      const res = smara
        ? await smaraStream("/v1/chat/stream", {
            message: text,
            conversation_id: conversationId,
            workspace_id: useProjectsStore.getState().currentProjectId ?? "default",
          }, abortController.signal)
        : await fetch(`${BASE_URL}/v1/memento/chat`, {
            method: "POST",
            credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              message: text,
              conversation_id: conversationId,
              attachment_ids: attachmentIds,
              deep_reasoning: get().deepReasoning,
              reasoning_model: get().deepReasoning ? get().reasoningModel : undefined,
              // F8: scope search_documents to the open Project, if any.
              project_id: useProjectsStore.getState().currentProjectId ?? undefined,
            }),
            signal: abortController.signal,
          });

      if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let accumulated = "";
      let retrievalTrace: RetrievalTrace | undefined; // legacy field kept for type compat
      // Captured when a tool result frame carries a `download` payload (e.g. generate_pdf).
      let pendingDownload: MessageDownload | undefined;
      // Captured when a tool result frame carries a `diagram` payload (create_diagram).
      let pendingDiagram: MessageDiagram | undefined;
      let pendingSkillProposal: MessageSkillProposal | undefined;
      // A birthday is stored in the account profile (not only in long-term
      // memory), so refresh /me once its safe tool call succeeds.
      let birthdaySaved = false;
      let buffer = "";
      let finished = false;

      while (!finished) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });

        // SSE frames are separated by a blank line
        let frameEnd: number;
        while ((frameEnd = buffer.indexOf("\n\n")) !== -1) {
          const frame = buffer.slice(0, frameEnd);
          buffer = buffer.slice(frameEnd + 2);

          for (const line of frame.split("\n")) {
            if (finished) break;
            if (!line.startsWith("data:")) continue;
            const raw = line.slice(5).trim();
            if (!raw) continue;
            let evt: any;
            try {
              evt = JSON.parse(raw);
            } catch {
              continue;
            }

            switch (evt.type) {
              case "phase":
                set({ currentPhase: evt.phase as AgentPhase });
                break;

              case "status":
                set((s) => ({
                  activity: [
                    ...s.activity.filter((item) => item.kind !== "status"),
                    {
                      kind: "status",
                      label: evt.label || "Working on it",
                      detail: typeof evt.detail === "string" ? evt.detail : undefined,
                      progress: typeof evt.progress === "number" ? evt.progress : undefined,
                    },
                  ],
                }));
                break;

              case "thought":
                set((s) => ({
                  activity: [...s.activity, { kind: "thought", text: evt.text }],
                }));
                break;

              case "memory_search":
                set((s) => ({
                  activity: [
                    ...s.activity,
                    { kind: "memory_search", query: evt.query, hits: evt.hits },
                  ],
                }));
                break;

              case "tool_call":
                set((s) => ({
                  activity: [
                    ...s.activity,
                    { kind: "tool_call", name: evt.name, args: evt.args || {} },
                  ],
                }));
                break;

              case "tool_result":
                if (
                  evt.download &&
                  typeof evt.download.url === "string" &&
                  typeof evt.download.filename === "string"
                ) {
                  pendingDownload = {
                    url: evt.download.url,
                    filename: evt.download.filename,
                    kind: typeof evt.download.kind === "string" ? evt.download.kind : "file",
                  };
                }
                if (evt.diagram && typeof evt.diagram.mermaid === "string") {
                  pendingDiagram = {
                    title: typeof evt.diagram.title === "string" ? evt.diagram.title : "Diagram",
                    mermaid: evt.diagram.mermaid,
                    kind: typeof evt.diagram.kind === "string" ? evt.diagram.kind : "diagram",
                  };
                }
                set((s) => ({
                  activity: [
                    ...s.activity,
                    {
                      kind: "tool_result",
                      name: evt.name,
                      ok: !!evt.ok,
                      preview: evt.preview || "",
                      citations: Array.isArray(evt.citations) ? evt.citations : undefined,
                    },
                  ],
                }));
                // Memory-mutating tools land here — bump the memory store's
                // dirty tick so the Memories panel re-fetches and shows the
                // new/updated/forgotten fact without a manual refresh.
                if (
                  evt.ok &&
                  typeof evt.name === "string" &&
                  _MEMORY_MUTATING_TOOLS.has(evt.name)
                ) {
                  useMemoryStore.getState().markDirty();
                }
                if (evt.ok && evt.name === "save_birthday") birthdaySaved = true;
                break;

              case "confirm_request":
                set((s) => ({
                  activity: [
                    ...s.activity,
                    {
                      kind: "confirm_request",
                      id: evt.confirm_id,
                      name: evt.name,
                      preview: evt.preview || "",
                      status: "pending",
                    },
                  ],
                }));
                break;

              case "confirm_resolved":
                set((s) => ({
                  activity: s.activity.map((a) =>
                    a.kind === "confirm_request" && a.id === evt.confirm_id
                      ? { ...a, status: evt.decision || "timeout" }
                      : a
                  ),
                }));
                break;

              case "skill_proposal": {
                pendingSkillProposal = {
                  id: evt.skill_id,
                  name: evt.name || "Reusable workflow",
                  description: evt.description || "",
                  tools: Array.isArray(evt.tools) ? evt.tools : [],
                  status: evt.auto_activated ? "approved" : "pending",
                };
                set((s) => ({
                  activity: [
                    ...s.activity,
                    { kind: "skill_proposal", ...pendingSkillProposal! },
                  ],
                }));
                break;
              }

              case "token":
                accumulated += evt.text;
                set({ streamingText: accumulated });
                break;

              case "stream_reset":
                // The writer's first attempt leaked/contradicted itself and
                // the backend is regenerating the reply from scratch — the
                // regeneration is the FULL answer, not a continuation.
                // Without this, the user sees the partial first attempt
                // followed by the complete second attempt glued together
                // (visible as the reply appearing to restart/duplicate
                // mid-stream).
                accumulated = "";
                set({ streamingText: "" });
                break;

              case "error":
                set((s) => ({
                  activity: [
                    ...s.activity,
                    {
                      kind: "error",
                      message: evt.message || "unknown error",
                      recoverable: !!evt.recoverable,
                    },
                  ],
                }));
                // For NON-recoverable errors (everything actually failed),
                // also surface a sticky toast — the in-activity badge alone is
                // easy to miss when the user is reading the chat.
                if (!evt.recoverable) {
                  // J2: the backend classifies LLM provider failures
                  // (invalid_key, no_credits, rate_limit, …). Key problems
                  // get a clearer title so users know it's THEIR key, fixable
                  // in Settings — not the app being broken.
                  const kind = evt.kind || "";
                  useNotificationsStore.getState().push({
                    kind: "error",
                    title: kind === "rate_limit"
                      ? "Temporarily rate-limited"
                      : ["invalid_key", "no_credits"].includes(kind)
                      ? "API key problem"
                      : "Couldn't complete that",
                    body: evt.message || "Something went wrong.",
                    ttl_ms: 0, // sticky
                  });
                }
                break;

              case "done": {
                // Empty-bubble safety net: if no tokens arrived (backend
                // failed at every fallback layer AND the new always-say-
                // something layer didn't kick in for some reason), render a
                // visible apology instead of a silent empty bubble.
                const text = accumulated.trim() || (
                  "I couldn't draft a reply this time — try sending again. " +
                  "(see notifications for the error)"
                );
                // Aggregate every citation URL/string emitted by the tools
                // this turn into a deduplicated "Sources" list pinned to
                // the assistant message — the trust handle that lets the
                // user verify any claim against an actual link.
                const activitySnapshot = get().activity;
                const seenSources = new Set<string>();
                const sources: string[] = [];
                for (const item of activitySnapshot) {
                  if (item.kind === "tool_result" && item.ok && item.citations) {
                    for (const c of item.citations) {
                      const trimmed = (c || "").trim();
                      if (trimmed && !seenSources.has(trimmed)) {
                        seenSources.add(trimmed);
                        sources.push(trimmed);
                      }
                    }
                  }
                }
                const assistantMsg: ConversationMessage = {
                  id: assistantId,
                  role: "assistant",
                  content: text,
                  timestamp: new Date().toISOString(),
                  retrievalTrace,
                  download: pendingDownload,
                  diagram: pendingDiagram,
                  skillProposal: pendingSkillProposal,
                  sources: sources.length > 0 ? sources : undefined,
                };
                set((s) => {
                  const next = [...s.messages, assistantMsg];
                  // Persist to conversations store
                  useConversationsStore.getState().save(sessionId, next, conversationId);
                  return {
                    messages: next,
                    isStreaming: false,
                    streamingText: "",
                    currentPhase: null,
                    activity: [],
                    _abortController: null,
                  };
                });
                finished = true;
                break;
              }
            }
          }
        }
      }

      if (finished && birthdaySaved) {
        try {
          const { useAuthStore } = await import("./authStore");
          await useAuthStore.getState().loadAccount();
        } catch {
          /* Profile will refresh on the next app load if this best-effort call fails. */
        }
      }

      // If the stream ended without a `done` frame (server crashed mid-stream),
      // still commit whatever we accumulated so the user sees a partial reply
      // rather than silently losing it.
      if (!finished && accumulated) {
        const assistantMsg: ConversationMessage = {
          id: assistantId,
          role: "assistant",
          content: accumulated,
          timestamp: new Date().toISOString(),
          retrievalTrace,
        };
        set((s) => {
          const next = [...s.messages, assistantMsg];
          useConversationsStore.getState().save(sessionId, next, conversationId);
          return {
            messages: next,
            isStreaming: false,
            streamingText: "",
            currentPhase: null,
            activity: [],
            _abortController: null,
          };
        });
        // Phase-1: after the first turn completes, refresh /me so the
        // warm greeting bubble (rendered while onboarded_at is null)
        // disappears for the next render. The backend stamps onboarded_at
        // in the chat handler's finally-block; this is the frontend's
        // signal to pick up the change without a full reload.
        try {
          const { useAuthStore } = await import("./authStore");
          const auth = useAuthStore.getState();
          if (auth.account && !auth.account.onboarded_at) {
            // Force a fresh /me fetch; resetting status to "loading"
            // lets loadAccount() re-run.
            useAuthStore.setState({ status: "loading" });
            await auth.loadAccount();
          }
        } catch {
          /* refresh is best-effort — next page load picks it up */
        }
      } else if (!finished) {
        // Stream closed cleanly (no network error) but neither a `done`
        // frame nor any tokens ever arrived — the backend dropped the
        // connection mid-turn without telling us why. Previously this reset
        // the UI in total silence: the typing indicator would just vanish
        // with no message and no explanation, indistinguishable from Smara
        // ignoring the user (the "stops mid-way" symptom). Surface it.
        useNotificationsStore.getState().push({
          kind: "error",
          title: "No reply came through",
          body: "The connection ended before Smara could answer. Try sending that again.",
          ttl_ms: 8000,
        });
        set({ isStreaming: false, streamingText: "", currentPhase: null, activity: [], _abortController: null });
      }
    } catch (err: any) {
      // AbortError = user clicked stop. Commit whatever tokens arrived so the
      // bubble isn't silently empty; don't log or show an error toast.
      if (err?.name === "AbortError") {
        const partial = get().streamingText.trim();
        if (partial) {
          const assistantMsg: ConversationMessage = {
            id: assistantId,
            role: "assistant",
            content: partial,
            timestamp: new Date().toISOString(),
          };
          set((s) => {
            const next = [...s.messages, assistantMsg];
            useConversationsStore.getState().save(sessionId, next, conversationId);
            return { messages: next, isStreaming: false, streamingText: "", currentPhase: null, activity: [], _abortController: null };
          });
        } else {
          set({ isStreaming: false, streamingText: "", currentPhase: null, activity: [], _abortController: null });
        }
        return;
      }
      // Surface the failure — otherwise the user sees the typing dots
      // disappear with no message and assumes Smara ignored them. A
      // 502/503/network failure almost always means a deploy is in
      // progress — say so instead of showing a raw "HTTP 502".
      if (isMaintenanceError(err)) {
        useNotificationsStore.getState().push({
          kind: "error",
          title: "Under maintenance",
          body: MAINTENANCE_MESSAGE,
          ttl_ms: 10000,
        });
      } else {
        const detail =
          err && typeof err === "object" && "message" in err
            ? String((err as { message?: unknown }).message ?? "")
            : "";
        useNotificationsStore.getState().push({
          kind: "error",
          title: "Couldn't reach Smara",
          body: detail
            ? `Stream failed (${detail.slice(0, 120)}). Check your connection and try again.`
            : "The connection dropped before a reply arrived. Try again.",
          ttl_ms: 8000,
        });
      }
      set({ isStreaming: false, streamingText: "", currentPhase: null, activity: [], _abortController: null });
    }
  },

  newChat: () => {
    // F8: stamp the active project (if any) onto the new conversation so
    // its Project workspace can list it under "Recent chats".
    const conv = useConversationsStore.getState().createNew(
      useProjectsStore.getState().currentProjectId ?? undefined,
    );
    set({
      sessionId: conv.id,
      conversationId: conv.conversationId,
      messages: [],
      isStreaming: false,
      streamingText: "",
      currentPhase: null,
      activity: [],
    });
  },

  loadConversation: (sessionId, conversationId, messages) => {
    set({
      sessionId,
      conversationId,
      messages,
      isStreaming: false,
      streamingText: "",
      currentPhase: null,
      activity: [],
    });
    useConversationsStore.getState().select(sessionId);
  },
}));
