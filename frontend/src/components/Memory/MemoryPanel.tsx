import { useState, useEffect, useCallback } from "react";
import { Search, RefreshCw } from "lucide-react";
import { useMemoryStore } from "@/stores/memoryStore";
import { apiClient } from "@/api/client";
import MemoryCard from "./MemoryCard";
import type { Memory } from "@/types/memory";

export default function MemoryPanel() {
  const approved = useMemoryStore((s) => s.approved);
  const setApproved = useMemoryStore((s) => s.setApproved);
  // Bumped by chatStore whenever a save/update/forget memory tool fires.
  // Watching it lets the panel auto-refresh after the agent writes a
  // memory, so the user doesn't have to close + reopen the panel.
  const dirtyTick = useMemoryStore((s) => s.dirtyTick);
  const [filter, setFilter] = useState("");
  const [refreshing, setRefreshing] = useState(false);

  const refetch = useCallback(async () => {
    setRefreshing(true);
    try {
      const memories = await apiClient.get<Memory[]>("/v1/memory");
      setApproved(memories);
    } catch {
      // Silent — keep showing the last good list.
    } finally {
      setRefreshing(false);
    }
  }, [setApproved]);

  // Re-fetch on mount AND whenever the dirty tick bumps. The panel stays
  // in sync with the agent's memory writes without polling.
  useEffect(() => {
    void refetch();
  }, [refetch, dirtyTick]);

  const visible = approved
    .filter((m) => !filter || m.text.toLowerCase().includes(filter.toLowerCase()))
    .slice()
    .sort((a, b) => {
      const ta = a.updatedAt ?? a.createdAt ?? "";
      const tb = b.updatedAt ?? b.createdAt ?? "";
      return tb.localeCompare(ta);
    });

  const hasFilter = filter.length > 0;

  return (
    <div className="flex flex-col h-full">
      {/* Search bar */}
      <div
        className="px-3 py-2.5 shrink-0"
        style={{ borderBottom: "1px solid var(--border-dim)" }}
      >
        <div
          className="flex items-center gap-2 rounded-lg px-2.5 transition-all duration-150"
          style={{
            background: "var(--bg-elevated)",
            border: "1px solid var(--border-default)",
          }}
        >
          <Search size={13} style={{ color: "var(--text-muted)", flexShrink: 0 }} />
          <input
            className="flex-1 bg-transparent py-1.5 text-xs focus:outline-none"
            style={{ color: "var(--text-primary)" }}
            placeholder="Search memories…"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
          {hasFilter ? (
            <span
              className="text-[10px] px-1.5 py-0.5 rounded-full"
              style={{
                background: "var(--accent-soft)",
                color: "var(--accent)",
                border: "1px solid var(--border-accent)",
              }}
            >
              {visible.length}
            </span>
          ) : (
            <span className="text-[10px]" style={{ color: "var(--text-dim)" }}>
              {approved.length}
            </span>
          )}
          <button
            onClick={() => void refetch()}
            disabled={refreshing}
            title="Refresh memories"
            className="grid place-items-center w-6 h-6 rounded-md transition-opacity"
            style={{
              color: "var(--text-muted)",
              opacity: refreshing ? 0.4 : 0.7,
            }}
            onMouseEnter={(e) => { (e.currentTarget as HTMLButtonElement).style.opacity = "1"; }}
            onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.opacity = refreshing ? "0.4" : "0.7"; }}
          >
            <RefreshCw size={11} className={refreshing ? "animate-spin" : ""} />
          </button>
        </div>
      </div>

      {/* Memory list */}
      <div className="flex-1 overflow-y-auto p-3 space-y-2 scrollbar-thin">
        {visible.length === 0 ? (
          <p
            className="text-sm text-center mt-12"
            style={{ color: "var(--text-dim)" }}
          >
            {approved.length === 0
              ? "No memories yet — start a conversation."
              : "No memories match your filter."}
          </p>
        ) : (
          visible.map((memory) => (
            <MemoryCard key={memory.id} memory={memory} mode="approved" />
          ))
        )}
      </div>
    </div>
  );
}
