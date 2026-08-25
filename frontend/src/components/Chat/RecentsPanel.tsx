import { useState, useRef, useEffect } from "react";
import { MoreHorizontal, Trash2, Pencil, Check, MessageSquare } from "lucide-react";
import { useConversationsStore } from "@/stores/conversationsStore";
import { useChatStore } from "@/stores/chatStore";
import clsx from "clsx";

export default function RecentsPanel({ onSelect }: { onSelect?: () => void }) {
  const { conversations, activeId, remove, rename } = useConversationsStore();
  const { loadConversation, sessionId } = useChatStore();

  function handleSelect(convId: string) {
    const conv = useConversationsStore.getState().select(convId);
    if (!conv) return;
    loadConversation(conv.id, conv.conversationId, conv.messages);
    onSelect?.();
  }

  if (conversations.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-1.5 px-4 text-center">
        <MessageSquare size={18} style={{ color: "var(--text-dim)", opacity: 0.5 }} />
        <p className="text-xs" style={{ color: "var(--text-dim)" }}>
          No conversations yet
        </p>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full overflow-y-auto scrollbar-thin py-1">
      {conversations.map((conv) => {
        const isActive = conv.id === (activeId ?? sessionId);
        return (
          <ConvItem
            key={conv.id}
            title={conv.title}
            isActive={isActive}
            onSelect={() => handleSelect(conv.id)}
            onDelete={() => remove(conv.id)}
            onRename={(t) => rename(conv.id, t)}
          />
        );
      })}
    </div>
  );
}

function ConvItem({
  title, isActive, onSelect, onDelete, onRename,
}: {
  title: string;
  isActive: boolean;
  onSelect: () => void;
  onDelete: () => void;
  onRename: (t: string) => void;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(title);
  const menuRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Close menu on outside click
  useEffect(() => {
    if (!menuOpen) return;
    const handler = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [menuOpen]);

  useEffect(() => {
    if (editing) inputRef.current?.focus();
  }, [editing]);

  const confirmRename = () => {
    if (draft.trim()) onRename(draft.trim());
    setEditing(false);
  };

  return (
    <div
      className={clsx(
        "group relative flex items-center gap-1 mx-2 px-3 py-2 rounded-lg cursor-pointer transition-colors duration-150",
      )}
      style={{
        background: isActive ? "var(--bg-elevated)" : "transparent",
      }}
      onClick={() => !editing && onSelect()}
      onMouseEnter={(e) => {
        if (!isActive) (e.currentTarget as HTMLDivElement).style.background = "var(--bg-elevated)";
      }}
      onMouseLeave={(e) => {
        if (!isActive) (e.currentTarget as HTMLDivElement).style.background = "transparent";
      }}
    >
      {/* Title / edit */}
      <div className="flex-1 min-w-0">
        {editing ? (
          <input
            ref={inputRef}
            className="w-full bg-transparent text-sm outline-none border-b"
            style={{
              color: "var(--text-primary)",
              borderColor: "var(--accent)",
            }}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") confirmRename();
              if (e.key === "Escape") { setEditing(false); setDraft(title); }
              e.stopPropagation();
            }}
            onClick={(e) => e.stopPropagation()}
          />
        ) : (
          <p
            className="text-sm truncate leading-snug"
            style={{ color: isActive ? "var(--text-primary)" : "var(--text-secondary)" }}
          >
            {title}
          </p>
        )}
      </div>

      {/* Confirm rename / menu */}
      {editing ? (
        <button
          onClick={(e) => { e.stopPropagation(); confirmRename(); }}
          className="shrink-0 p-1 rounded"
          style={{ color: "var(--accent)" }}
        >
          <Check size={13} />
        </button>
      ) : (
        <button
          className="shrink-0 p-1 rounded opacity-0 group-hover:opacity-100 transition-opacity"
          style={{ color: "var(--text-muted)" }}
          onClick={(e) => { e.stopPropagation(); setMenuOpen(!menuOpen); }}
          title="Options"
        >
          <MoreHorizontal size={14} />
        </button>
      )}

      {/* Dropdown menu */}
      {menuOpen && (
        <div
          ref={menuRef}
          className="absolute right-1 top-9 z-50 rounded-lg py-1 min-w-[130px]"
          style={{
            background: "var(--bg-elevated)",
            border: "1px solid var(--border-default)",
            boxShadow: "var(--shadow-lg)",
          }}
          onClick={(e) => e.stopPropagation()}
        >
          <MenuItem
            icon={<Pencil size={12} />}
            label="Rename"
            onClick={() => { setMenuOpen(false); setEditing(true); }}
          />
          <MenuItem
            icon={<Trash2 size={12} />}
            label="Delete"
            danger
            onClick={() => { setMenuOpen(false); onDelete(); }}
          />
        </div>
      )}
    </div>
  );
}

function MenuItem({
  icon, label, onClick, danger = false,
}: {
  icon: React.ReactNode;
  label: string;
  onClick: () => void;
  danger?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      className="flex items-center gap-2 w-full px-3 py-1.5 text-xs transition-colors"
      style={{ color: danger ? "#f87171" : "var(--text-secondary)" }}
      onMouseEnter={(e) =>
        ((e.currentTarget as HTMLButtonElement).style.background = "var(--bg-base)")
      }
      onMouseLeave={(e) =>
        ((e.currentTarget as HTMLButtonElement).style.background = "transparent")
      }
    >
      {icon}
      {label}
    </button>
  );
}
