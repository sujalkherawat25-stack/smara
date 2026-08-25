import { memo } from "react";
import {
  getSmoothStepPath,
  EdgeLabelRenderer,
  type EdgeProps,
} from "@xyflow/react";

function RelationEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  label,
  data,
  animated,
  selected,
}: EdgeProps) {
  const [edgePath, labelX, labelY] = getSmoothStepPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
    borderRadius: 12,
  });

  const isValid = (data as { valid?: boolean })?.valid !== false;
  const isActive = animated || selected;

  const strokeColor = isActive
    ? "#22d3ee"
    : isValid
    ? "rgba(148,163,184,0.4)"
    : "rgba(239,68,68,0.5)";

  const strokeWidth = isActive ? 1.8 : 1.2;

  return (
    <>
      {/* Glow layer for active edges */}
      {isActive && (
        <path
          d={edgePath}
          fill="none"
          stroke="#22d3ee"
          strokeWidth={6}
          strokeOpacity={0.08}
          strokeLinecap="round"
        />
      )}

      {/* Main path */}
      <path
        id={id}
        d={edgePath}
        fill="none"
        stroke={strokeColor}
        strokeWidth={strokeWidth}
        strokeDasharray={!isValid ? "5 3" : isActive ? "6 0" : undefined}
        strokeLinecap="round"
        style={{
          transition: "stroke 0.2s, stroke-width 0.2s",
        }}
        markerEnd={isActive ? "url(#arrow-active)" : "url(#arrow)"}
      />

      {/* Animated flow overlay for active edges */}
      {isActive && (
        <path
          d={edgePath}
          fill="none"
          stroke="rgba(34,211,238,0.7)"
          strokeWidth={1.5}
          strokeDasharray="6 12"
          strokeLinecap="round"
          className="edge-animated"
        />
      )}

      {label && (
        <EdgeLabelRenderer>
          <div
            style={{
              transform: `translate(-50%, -50%) translate(${labelX}px,${labelY}px)`,
              pointerEvents: "none",
            }}
            className="absolute"
          >
            <span
              className="px-1.5 py-0.5 rounded text-[10px] font-mono"
              style={{
                background: isActive ? "rgba(34,211,238,0.1)" : "rgba(12,18,32,0.9)",
                border: `1px solid ${isActive ? "rgba(34,211,238,0.3)" : "rgba(255,255,255,0.08)"}`,
                color: isActive ? "#22d3ee" : "rgba(148,163,184,0.8)",
                backdropFilter: "blur(4px)",
              }}
            >
              {String(label)}
            </span>
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
}

export default memo(RelationEdge);
