import dagre from "dagre";
import type { Node, Edge } from "@xyflow/react";

const NODE_W = 160;
const NODE_H = 56;

/**
 * Lay nodes out with dagre so they never overlap.
 * `direction` can be "LR" (left-to-right) or "TB" (top-to-bottom).
 */
export function layoutGraph<N extends Node, E extends Edge>(
  nodes: N[],
  edges: E[],
  direction: "LR" | "TB" = "LR"
): N[] {
  if (nodes.length === 0) return nodes;

  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({
    rankdir: direction,
    nodesep: 60,
    ranksep: 90,
    marginx: 30,
    marginy: 30,
  });

  nodes.forEach((n) => g.setNode(n.id, { width: NODE_W, height: NODE_H }));
  edges.forEach((e) => g.setEdge(e.source, e.target));

  dagre.layout(g);

  return nodes.map((n) => {
    const pos = g.node(n.id);
    return {
      ...n,
      position: { x: pos.x - NODE_W / 2, y: pos.y - NODE_H / 2 },
      // Marks them as already-positioned so React Flow doesn't auto-place
      sourcePosition: (direction === "LR" ? "right" : "bottom") as any,
      targetPosition: (direction === "LR" ? "left" : "top") as any,
    };
  });
}
