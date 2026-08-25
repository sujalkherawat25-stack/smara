import { useState } from "react";
import { CheckCheck, X, ChevronDown, ChevronUp } from "lucide-react";
import { useMemoryStore } from "@/stores/memoryStore";
import { useMemoryMutations } from "@/hooks/useMemoryMutations";
import MemoryCard from "./MemoryCard";

export default function PendingDrawer() {
  const pending = useMemoryStore((s) => s.pending);
  const { approveAll, rejectAll } = useMemoryMutations();
  const [expanded, setExpanded] = useState(false);

  if (pending.length === 0) return null;

  return (
    <div className="animate-drawer-up border-t border-amber-500/40 bg-gray-900/95 backdrop-blur-sm p-3">
      <div className="flex items-center justify-between mb-2">
        <span className="text-sm font-medium text-amber-400">
          Agent wants to remember {pending.length} thing{pending.length !== 1 ? "s" : ""}
        </span>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setExpanded((v) => !v)}
            className="flex items-center gap-1 px-2 py-1 text-xs bg-gray-700 hover:bg-gray-600 rounded transition-colors text-gray-300"
          >
            {expanded ? <ChevronDown size={12} /> : <ChevronUp size={12} />}
            {expanded ? "Collapse" : "Review"}
          </button>
          <button
            onClick={approveAll}
            className="flex items-center gap-1 px-3 py-1 text-xs bg-green-700 hover:bg-green-600 rounded transition-colors"
          >
            <CheckCheck size={13} />
            Approve All
          </button>
          <button
            onClick={rejectAll}
            className="flex items-center gap-1 px-3 py-1 text-xs bg-red-900 hover:bg-red-800 rounded transition-colors"
          >
            <X size={13} />
            Dismiss
          </button>
        </div>
      </div>

      {expanded && (
        <div className="space-y-2 max-h-48 overflow-y-auto scrollbar-thin mt-2">
          {pending.map((mem) => (
            <MemoryCard key={mem.id} memory={mem} mode="pending" />
          ))}
        </div>
      )}
    </div>
  );
}
