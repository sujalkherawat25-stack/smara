/**
 * Notifications — top-right toast stack for async backend events.
 *
 * Mounted once at the app root. Listens to useNotificationsStore and renders
 * a stack of toasts. Each toast has a colour per kind, optional action
 * button, and a manual close.
 */

import { useNotificationsStore, type ToastNotification } from "@/stores/notificationsStore";
import { useChatStore } from "@/stores/chatStore";
import { useConversationsStore } from "@/stores/conversationsStore";
import { Bell, CheckCircle2, AlertCircle, X } from "lucide-react";
import type { ConversationMessage } from "@/types/memory";

export default function Notifications() {
  const toasts = useNotificationsStore((s) => s.toasts);
  const dismiss = useNotificationsStore((s) => s.dismiss);

  if (toasts.length === 0) return null;

  return (
    <div
      className="fixed top-16 right-4 z-50 flex flex-col gap-2 pointer-events-none"
      style={{ maxWidth: "380px" }}
    >
      {toasts.map((t) => (
        <Toast key={t.id} toast={t} onDismiss={() => dismiss(t.id)} />
      ))}
    </div>
  );
}

function Toast({ toast, onDismiss }: { toast: ToastNotification; onDismiss: () => void }) {
  const { color, icon } = styleFor(toast.kind);

  return (
    <div
      className="pointer-events-auto rounded-xl p-3 shadow-lg animate-fade-in"
      style={{
        background: "var(--bg-elevated)",
        border: `1px solid ${color}`,
        boxShadow: "0 10px 30px rgba(0,0,0,0.3)",
      }}
    >
      <div className="flex items-start gap-2.5">
        <div className="shrink-0 mt-0.5" style={{ color }}>
          {icon}
        </div>
        <div className="flex-1 min-w-0">
          <div className="text-sm font-medium" style={{ color: "var(--text-primary)" }}>
            {toast.title}
          </div>
          <div className="text-xs mt-1 leading-snug" style={{ color: "var(--text-secondary)" }}>
            {toast.body}
          </div>
          {toast.action && (
            <button
              onClick={() => handleAction(toast, onDismiss)}
              className="mt-2 px-2.5 py-1 rounded-md text-xs transition-colors"
              style={{
                background: `color-mix(in srgb, ${color} 20%, transparent)`,
                color,
                border: `1px solid color-mix(in srgb, ${color} 35%, transparent)`,
              }}
            >
              {toast.action.label}
            </button>
          )}
        </div>
        <button
          onClick={onDismiss}
          className="shrink-0 text-xs opacity-50 hover:opacity-100 transition-opacity"
          style={{ color: "var(--text-muted)" }}
          aria-label="Dismiss"
        >
          <X size={14} />
        </button>
      </div>
    </div>
  );
}

function styleFor(kind: ToastNotification["kind"]) {
  switch (kind) {
    case "reminder":
      return { color: "var(--accent)", icon: <Bell size={16} /> };
    case "task_result":
      return { color: "var(--accent2)", icon: <CheckCircle2 size={16} /> };
    case "error":
      return { color: "#f87171", icon: <AlertCircle size={16} /> };
    default:
      return { color: "var(--text-secondary)", icon: <Bell size={16} /> };
  }
}

// Action handler — currently the only action is "view task result", which
// opens a new conversation pre-filled with the agent reply.
function handleAction(toast: ToastNotification, onDismiss: () => void) {
  const payload = toast.action?.payload || {};
  if (toast.kind === "task_result" && payload.kind === "open_new_chat") {
    const conv = useConversationsStore.getState().createNew();
    const replyText = String(payload.reply || "");
    const taskText = String(payload.task_text || "");
    const messages: ConversationMessage[] = [
      {
        id: crypto.randomUUID(),
        role: "user",
        content: `(scheduled task) ${taskText}`,
        timestamp: new Date().toISOString(),
      },
      {
        id: crypto.randomUUID(),
        role: "assistant",
        content: replyText,
        timestamp: new Date().toISOString(),
      },
    ];
    useConversationsStore.getState().save(conv.id, messages, conv.conversationId);
    useChatStore.getState().loadConversation(conv.id, conv.conversationId, messages);
  }
  onDismiss();
}
