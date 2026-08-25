import { memo } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import { TYPE_COLORS } from "./GraphCanvas";

interface EntityNodeData {
  label: string;
  entityType: string;
  memoryCount: number;
  createdAt: string;
  [key: string]: unknown;
}

function EntityNode({ data, selected }: NodeProps) {
  const d = data as EntityNodeData;
  const color = TYPE_COLORS[d.entityType] ?? "#64748b";
  const glow = `${color}40`;
  const glowStrong = `${color}25`;

  return (
    <div
      className="relative px-3.5 py-2.5 rounded-xl text-center min-w-[90px] transition-all duration-200"
      style={{
        background: selected
          ? `linear-gradient(135deg, ${glowStrong} 0%, var(--node-bg) 100%)`
          : "var(--node-bg)",
        border: `1px solid ${selected ? color + "60" : "var(--border-dim)"}`,
        boxShadow: selected
          ? `0 0 0 1px ${color}40, 0 0 20px ${color}20, var(--shadow-md)`
          : "var(--shadow-sm)",
        backdropFilter: "blur(8px)",
      }}
    >
      <Handle type="target" position={Position.Top} style={{ opacity: 0, pointerEvents: "none" }} />

      {/* Color dot with glow */}
      <div className="relative flex items-center justify-center mx-auto mb-1.5 w-5 h-5">
        <div
          className="absolute inset-0 rounded-full opacity-40"
          style={{ background: color, filter: "blur(4px)" }}
        />
        <div
          className="relative w-2.5 h-2.5 rounded-full"
          style={{
            background: `radial-gradient(circle at 35% 35%, white 0%, ${color} 50%)`,
            boxShadow: `0 0 6px ${glow}`,
          }}
        />
      </div>

      <div className="text-xs font-semibold truncate max-w-[120px]" style={{ color: "var(--text-primary)" }}>
        {d.label}
      </div>
      <div className="text-[10px] mt-0.5 flex items-center justify-center gap-1">
        <span style={{ color: color + "cc" }}>{d.entityType}</span>
        {(d.memoryCount ?? 0) > 0 && (
          <>
            <span style={{ color: "var(--text-dim)" }}>·</span>
            <span style={{ color: "var(--text-muted)" }}>{d.memoryCount}</span>
          </>
        )}
      </div>

      <Handle type="source" position={Position.Bottom} style={{ opacity: 0, pointerEvents: "none" }} />
    </div>
  );
}

export default memo(EntityNode);
