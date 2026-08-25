import { useEffect, useState } from "react";
import { X, Link } from "lucide-react";
import { apiClient } from "@/api/client";
import { TYPE_COLORS } from "./GraphCanvas";
import type { EntitySubgraph } from "@/types/graph";

interface Props {
  entityId: string;
  onClose: () => void;
}

export default function NodeTooltip({ entityId, onClose }: Props) {
  const [data, setData] = useState<EntitySubgraph | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    setData(null);
    apiClient
      .get<EntitySubgraph>(`/v1/graph/entity/${entityId}`)
      .then(setData)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [entityId]);

  const entityType = data?.entity?.data?.entityType ?? "Other";
  const color = TYPE_COLORS[entityType] ?? "#64748b";

  return (
    <div
      className="absolute z-20 right-3 top-14 w-68 rounded-xl text-xs animate-fade-in"
      style={{
        width: "272px",
        background: "var(--tooltip-bg)",
        border: `1px solid ${color}25`,
        backdropFilter: "blur(16px)",
        boxShadow: `0 8px 32px rgba(0,0,0,0.5), 0 0 0 1px rgba(255,255,255,0.04), 0 0 20px ${color}10`,
      }}
    >
      {/* Header */}
      <div
        className="flex items-center justify-between px-3 py-2.5"
        style={{ borderBottom: "1px solid var(--border-dim)" }}
      >
        <div className="flex items-center gap-2 min-w-0">
          <div
            className="w-2 h-2 rounded-full shrink-0"
            style={{ background: color, boxShadow: `0 0 6px ${color}80` }}
          />
          <span className="font-semibold truncate" style={{ color: "var(--text-primary)" }}>
            {data?.entity?.data?.label ?? "…"}
          </span>
          <span
            className="text-[10px] px-1.5 py-0.5 rounded shrink-0"
            style={{
              background: `${color}15`,
              color: color,
              border: `1px solid ${color}30`,
            }}
          >
            {entityType}
          </span>
        </div>
        <button
          onClick={onClose}
          className="p-1 rounded hover:bg-white/5 transition-colors shrink-0"
          style={{ color: "rgba(100,116,139,0.7)" }}
        >
          <X size={12} />
        </button>
      </div>

      <div className="p-3 space-y-3">
        {loading && (
          <div className="flex items-center gap-2" style={{ color: "rgba(100,116,139,0.6)" }}>
            <div
              className="w-3 h-3 rounded-full border border-t-transparent animate-spin"
              style={{ borderColor: `${color}40`, borderTopColor: color }}
            />
            <span>Loading…</span>
          </div>
        )}

        {!loading && data && (
          <>
            {/* Connected entities */}
            {data.neighbours.length > 0 && (
              <div>
                <div
                  className="flex items-center gap-1.5 mb-1.5 text-[10px] uppercase tracking-wider font-medium"
                  style={{ color: "rgba(100,116,139,0.6)" }}
                >
                  <Link size={9} />
                  Connected
                </div>
                <div className="flex flex-wrap gap-1">
                  {data.neighbours.map((n) => (
                    <span
                      key={n.id}
                      className="px-1.5 py-0.5 rounded text-[10px]"
                      style={{
                        background: "var(--bg-elevated)",
                        border: "1px solid var(--border-dim)",
                        color: "var(--text-secondary)",
                      }}
                    >
                      {n.data.label}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {/* Memories */}
            <div>
              <div
                className="text-[10px] uppercase tracking-wider font-medium mb-1.5"
                style={{ color: "rgba(100,116,139,0.6)" }}
              >
                Memories ({data.memories.length})
              </div>
              {data.memories.length === 0 ? (
                <p style={{ color: "rgba(100,116,139,0.4)" }}>No memories linked</p>
              ) : (
                <div className="space-y-1.5 max-h-40 overflow-y-auto scrollbar-thin">
                  {data.memories.map((m) => (
                    <div
                      key={m.id}
                      className="pl-2 py-1 rounded-r"
                      style={{
                        borderLeft: `2px solid ${color}50`,
                        background: "var(--accent-dim)",
                      }}
                    >
                      <p style={{ color: "var(--text-secondary)", lineHeight: "1.4" }}>{m.text}</p>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
