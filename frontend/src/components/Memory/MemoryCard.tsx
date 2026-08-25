import { useState } from "react";
import { Trash2, Check, X, Pencil } from "lucide-react";
import { useMemoryMutations } from "@/hooks/useMemoryMutations";
import type { Memory, PendingMemory } from "@/types/memory";

// Only show op badge for non-standard operations (UPDATE/DELETE from legacy data).
// ADD is the pipeline default and adds zero information on every card.
const OP_STYLES: Record<string, { color: string; border: string; label: string }> = {
  UPDATE: { color: "#fbbf24", border: "rgba(245,158,11,0.25)", label: "updated" },
  DELETE: { color: "#f87171", border: "rgba(239,68,68,0.25)",  label: "deleted" },
  NOOP:   { color: "#64748b", border: "rgba(71,85,105,0.20)",  label: "noop"    },
};

function relativeTime(iso: string): string {
  // Backend returns UTC without 'Z' — force UTC parsing to avoid local-offset errors
  const utc = iso.endsWith("Z") || iso.includes("+") ? iso : iso + "Z";
  const diff = Date.now() - new Date(utc).getTime();
  const mins  = Math.floor(diff / 60_000);
  const hours = Math.floor(diff / 3_600_000);
  const days  = Math.floor(diff / 86_400_000);
  if (mins  < 1)  return "just now";
  if (mins  < 60) return `${mins}m ago`;
  if (hours < 24) return `${hours}h ago`;
  if (days  < 7)  return `${days}d ago`;
  return new Date(iso).toLocaleDateString([], { month: "short", day: "numeric" });
}

interface Props {
  memory: Memory | PendingMemory;
  mode?: "approved" | "pending";
}

export default function MemoryCard({ memory, mode = "approved" }: Props) {
  const [editing, setEditing] = useState(false);
  const [editText, setEditText] = useState(memory.text);
  const { approveOne, rejectOne, editPending, deleteMemory } = useMemoryMutations();

  const handleEditConfirm = async () => {
    await editPending(memory.id, editText);
    setEditing(false);
  };

  const timeIso =
    "updatedAt" in memory && memory.updatedAt
      ? memory.updatedAt
      : "createdAt" in memory
      ? memory.createdAt
      : "extractedAt" in memory
      ? (memory as PendingMemory).extractedAt
      : "";

  const op = "operation" in memory ? (memory as Memory).operation : undefined;
  // Only look up style for non-ADD ops; ADD gets the default emerald stripe
  const opStyle = op && op !== "ADD" ? OP_STYLES[op] : undefined;

  const supersededBy = "supersededBy" in memory ? (memory as Memory).supersededBy : undefined;
  const decayFactor = "decayFactor" in memory ? (memory as Memory).decayFactor : undefined;
  // 0.3 is a display-only threshold (separate from settings.decay_prune_threshold
  // which is much lower — that one controls actual pruning). This just flags
  // "fading" early enough to be a useful heads-up before a memory is barely
  // retrievable at all.
  const isFading = !supersededBy && typeof decayFactor === "number" && decayFactor < 0.3;

  const stripeColor = supersededBy
    ? "#f87171"
    : opStyle
    ? opStyle.color
    : "var(--accent2)";

  return (
    <div
      className="group relative rounded-xl overflow-hidden transition-all duration-200"
      style={{
        background: "var(--bg-card)",
        border: "1px solid var(--border-dim)",
      }}
      onMouseEnter={(e) => {
        (e.currentTarget as HTMLDivElement).style.borderColor = "var(--border-default)";
        (e.currentTarget as HTMLDivElement).style.boxShadow = "var(--shadow-sm)";
      }}
      onMouseLeave={(e) => {
        (e.currentTarget as HTMLDivElement).style.borderColor = "var(--border-dim)";
        (e.currentTarget as HTMLDivElement).style.boxShadow = "none";
      }}
    >
      {/* Left accent stripe — emerald for ADD, colored for legacy ops */}
      <div
        className="absolute left-0 top-0 bottom-0 w-0.5 rounded-l-xl"
        style={{ background: stripeColor, opacity: 0.5 }}
      />

      <div className="px-3.5 py-3 pl-4">
        {editing ? (
          <div className="flex gap-2">
            <input
              autoFocus
              className="flex-1 rounded-lg px-2.5 py-1.5 text-sm focus:outline-none"
              style={{
                background: "var(--bg-input)",
                border: "1px solid var(--border-accent)",
                color: "var(--text-primary)",
              }}
              value={editText}
              onChange={(e) => setEditText(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleEditConfirm()}
            />
            <button
              onClick={handleEditConfirm}
              className="p-1.5 rounded-md transition-colors"
              style={{ color: "#34d399" }}
            >
              <Check size={13} />
            </button>
            <button
              onClick={() => setEditing(false)}
              className="p-1.5 rounded-md transition-colors"
              style={{ color: "var(--text-muted)" }}
            >
              <X size={13} />
            </button>
          </div>
        ) : (
          <p className="text-sm leading-snug" style={{ color: "var(--text-secondary)" }}>
            {memory.text}
          </p>
        )}

        {/* Footer */}
        <div className="flex items-center justify-between mt-2">
          <div className="flex items-center gap-2">
            {supersededBy ? (
              <span
                className="text-[10px] font-mono px-1.5 py-0.5 rounded"
                title="A newer memory replaced this — it's decaying fast and being deprioritised in recall, but not deleted."
                style={{
                  color: "#f87171",
                  border: "1px solid rgba(239,68,68,0.25)",
                  background: "rgba(239,68,68,0.10)",
                }}
              >
                superseded
              </span>
            ) : isFading ? (
              <span
                className="text-[10px] font-mono px-1.5 py-0.5 rounded"
                title={`Decay factor ${decayFactor!.toFixed(2)} — rarely surfaced in recall anymore.`}
                style={{
                  color: "#fbbf24",
                  border: "1px solid rgba(245,158,11,0.25)",
                  background: "rgba(245,158,11,0.10)",
                }}
              >
                fading
              </span>
            ) : null}
            {/* Only show badge for non-ADD legacy ops */}
            {opStyle && op && (
              <span
                className="text-[10px] font-mono px-1.5 py-0.5 rounded"
                style={{
                  color: opStyle.color,
                  border: `1px solid ${opStyle.border}`,
                  background: `${opStyle.color}10`,
                }}
              >
                {opStyle.label}
              </span>
            )}
            {timeIso && (
              <span className="text-[11px]" style={{ color: "var(--text-dim)" }}>
                {relativeTime(timeIso)}
              </span>
            )}
          </div>

          {/* Action buttons — appear on hover */}
          <div className="flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity duration-150">
            {mode === "approved" && (
              <ActionBtn
                onClick={() => deleteMemory(memory.id)}
                title="Delete"
                hoverColor="rgba(239,68,68,0.12)"
                color="#f87171"
              >
                <Trash2 size={11} />
              </ActionBtn>
            )}
            {mode === "pending" && (
              <>
                <ActionBtn
                  onClick={() => approveOne(memory.id)}
                  title="Approve"
                  hoverColor="rgba(16,185,129,0.12)"
                  color="#34d399"
                >
                  <Check size={11} />
                </ActionBtn>
                <ActionBtn
                  onClick={() => setEditing(true)}
                  title="Edit"
                  hoverColor="rgba(34,211,238,0.12)"
                  color="var(--accent)"
                >
                  <Pencil size={11} />
                </ActionBtn>
                <ActionBtn
                  onClick={() => rejectOne(memory.id)}
                  title="Reject"
                  hoverColor="rgba(239,68,68,0.12)"
                  color="#f87171"
                >
                  <X size={11} />
                </ActionBtn>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function ActionBtn({
  children, onClick, title, hoverColor, color,
}: {
  children: React.ReactNode;
  onClick: () => void;
  title: string;
  hoverColor: string;
  color: string;
}) {
  return (
    <button
      onClick={onClick}
      title={title}
      className="p-1.5 rounded-md transition-all duration-150"
      style={{ color: "var(--text-muted)" }}
      onMouseEnter={(e) => {
        const b = e.currentTarget as HTMLButtonElement;
        b.style.background = hoverColor;
        b.style.color = color;
      }}
      onMouseLeave={(e) => {
        const b = e.currentTarget as HTMLButtonElement;
        b.style.background = "transparent";
        b.style.color = "var(--text-muted)";
      }}
    >
      {children}
    </button>
  );
}
