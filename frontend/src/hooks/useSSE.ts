import { useEffect, useRef, useState } from "react";
import { useMemoryStore } from "@/stores/memoryStore";
import { useGraphStore } from "@/stores/graphStore";
import {
  ensureNotificationPermission,
  showBrowserNotification,
  useNotificationsStore,
} from "@/stores/notificationsStore";
import { useChatStore } from "@/stores/chatStore";
import { useConversationsStore } from "@/stores/conversationsStore";
import type { ConversationMessage } from "@/types/memory";

// Token TTL (server-issued) is 5 min; refresh comfortably before that.
const TOKEN_REFRESH_MS = 4 * 60 * 1000;

async function mintSseToken(): Promise<string> {
  // F3: cookie auth — backend reads mem_session and mints a token for that account.
  const r = await fetch("/v1/auth/sse-token", {
    method: "POST",
    credentials: "include",
  });
  if (!r.ok) throw new Error(`sse-token mint failed: ${r.status}`);
  const data = await r.json();
  return data.token as string;
}

export function useSSE() {
  const [connected, setConnected] = useState(false);
  const applyOperation = useMemoryStore((s) => s.applyOperation);
  const setPending = useMemoryStore((s) => s.setPending);
  const addNodes = useGraphStore((s) => s.addNodes);
  const addEdges = useGraphStore((s) => s.addEdges);
  const updateNode = useGraphStore((s) => s.updateNode);

  // Hold the live EventSource so we can swap it on token refresh / unmount.
  const esRef = useRef<EventSource | null>(null);
  const refreshTimer = useRef<number | null>(null);
  const cancelled = useRef(false);

  useEffect(() => {
    cancelled.current = false;

    // Ask the browser for notification permission once, on first SSE mount.
    // No-op if already granted/denied or running outside a browser.
    ensureNotificationPermission();

    const wire = (es: EventSource) => {
      es.onopen = () => setConnected(true);
      es.onerror = () => setConnected(false);
      es.onmessage = (e) => {
        try {
          const event = JSON.parse(e.data);
          switch (event.type) {
            case "memory_candidates":
              // Auto-approval mode: candidates flow straight to ADD/UPDATE/NOOP,
              // we never queue them. Kept as a no-op for backward compat.
              setPending([]);
              break;
            case "memory_approved":
              if (event.memory && event.operation) {
                applyOperation(event.memory, event.operation);
              }
              break;
            case "graph_delta":
              if (event.nodes_added?.length) addNodes(event.nodes_added);
              if (event.edges_added?.length) addEdges(event.edges_added);
              break;
            case "graph_node_updated":
              if (event.node) updateNode(event.node);
              break;

            case "reminder_fired":
              handleReminderFired(event);
              break;

            case "task_result":
              handleTaskResult(event);
              break;
          }
        } catch {
          // ignore parse errors
        }
      };
    };

    const connect = async () => {
      try {
        const token = await mintSseToken();
        if (cancelled.current) return;
        const es = new EventSource(`/v1/events?token=${encodeURIComponent(token)}`);
        esRef.current?.close();
        esRef.current = es;
        wire(es);
      } catch (err) {
        // Mint failed (server down, network) — retry after a short delay.
        setConnected(false);
        if (!cancelled.current) {
          window.setTimeout(connect, 5000);
        }
      }
    };

    connect();
    // Refresh the SSE connection with a fresh token before the old one expires.
    refreshTimer.current = window.setInterval(connect, TOKEN_REFRESH_MS);

    return () => {
      cancelled.current = true;
      if (refreshTimer.current) window.clearInterval(refreshTimer.current);
      esRef.current?.close();
      esRef.current = null;
    };
  }, []);

  return { connected };
}


// ── Reminder + scheduled-task event handlers ──────────────────────────────

function handleReminderFired(event: {
  text?: string;
  reminder_id?: string;
  conversation_id?: string | null;
}) {
  const title = "Reminder";
  const body = event.text || "(no text)";

  // Always push the in-app toast — works even without browser permission.
  useNotificationsStore.getState().push({
    kind: "reminder",
    title,
    body,
    ttl_ms: 12000,
  });

  // Browser notification (works when tab is in background, IF permission granted).
  showBrowserNotification(`⏰ ${title}`, body);

  // If the reminder was created from the user's current conversation, also
  // append a system bubble there so it shows up in the chat scroll.
  appendSystemMessage(
    event.conversation_id ?? null,
    `⏰ Reminder: ${body}`,
  );
}

function handleTaskResult(event: {
  task_id?: string;
  task_text?: string;
  reply?: string;
  target?: "current" | "new";
  conversation_id?: string;
}) {
  const taskText = event.task_text || "(scheduled task)";
  const reply = event.reply || "(no reply)";

  if (event.target === "new") {
    // Don't auto-open the chat — let the user click "Open" so we don't
    // hijack focus while they're reading something.
    useNotificationsStore.getState().push({
      kind: "task_result",
      title: "Scheduled task done",
      body: `“${truncate(taskText, 80)}” — tap to open the result.`,
      ttl_ms: 0,           // sticky until dismissed/actioned
      action: {
        label: "Open in new chat",
        payload: {
          kind: "open_new_chat",
          task_text: taskText,
          reply,
        },
      },
    });
    showBrowserNotification("Scheduled task done", truncate(reply, 120));
    return;
  }

  // target === "current": append directly to the originating conversation.
  useNotificationsStore.getState().push({
    kind: "task_result",
    title: "Scheduled task done",
    body: truncate(reply, 140),
    ttl_ms: 12000,
  });
  showBrowserNotification("Scheduled task done", truncate(reply, 120));

  appendSystemMessage(event.conversation_id ?? null, `🤖 Scheduled task — ${taskText}\n\n${reply}`);
}


function appendSystemMessage(targetConvId: string | null, text: string) {
  // We append to the currently-active chat only if its backend conversation_id
  // matches the event's. Otherwise we'd be writing to the wrong conversation.
  const chat = useChatStore.getState();
  if (!targetConvId || chat.conversationId !== targetConvId) {
    // The event isn't for the open chat — toast/notification is enough.
    return;
  }
  const systemMsg: ConversationMessage = {
    id: crypto.randomUUID(),
    role: "assistant",
    content: text,
    timestamp: new Date().toISOString(),
  };
  // Persist into both stores so a refresh doesn't lose it.
  // Use functional updater to avoid race condition against concurrent token writes.
  useChatStore.setState((s) => {
    const nextMessages = [...s.messages, systemMsg];
    useConversationsStore.getState().save(s.sessionId, nextMessages, s.conversationId);
    return { messages: nextMessages };
  });
}


function truncate(s: string, n: number): string {
  return s.length > n ? s.slice(0, n - 1) + "…" : s;
}
