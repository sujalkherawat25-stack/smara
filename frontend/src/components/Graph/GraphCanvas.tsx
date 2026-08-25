import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ReactFlow,
  ReactFlowProvider,
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  useReactFlow,
  type Node,
  type Edge,
  type NodeMouseHandler,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import EntityNode from "./EntityNode";
import RelationEdge from "./RelationEdge";
import NodeTooltip from "./NodeTooltip";
import { useGraphStore } from "@/stores/graphStore";
import { apiClient } from "@/api/client";
import { layoutGraph } from "./layout";
import { LayoutGrid, Search, Maximize2 } from "lucide-react";

export const TYPE_COLORS: Record<string, string> = {
  Person:     "#a78bfa",   // violet  — primary accent
  Location:   "#34d399",   // emerald — secondary accent
  Preference: "#f472b6",   // pink
  Event:      "#fbbf24",   // amber
  Concept:    "#60a5fa",   // sky
  Health:     "#f87171",   // red
  Object:     "#94a3b8",   // slate
  Other:      "#6b5f8a",   // muted violet
};

const nodeTypes = { entityNode: EntityNode };
const edgeTypes = { relationEdge: RelationEdge };

/* SVG arrow markers injected into the React Flow SVG defs */
function ArrowDefs() {
  return (
    <svg style={{ position: "absolute", width: 0, height: 0 }}>
      <defs>
        <marker id="arrow" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto">
          <path d="M0,0 L0,6 L8,3 z" fill="rgba(167,139,250,0.35)" />
        </marker>
        <marker id="arrow-active" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto">
          <path d="M0,0 L0,6 L8,3 z" fill="#a78bfa" />
        </marker>
      </defs>
    </svg>
  );
}

function InnerGraph() {
  const storeNodes = useGraphStore((s) => s.nodes);
  const storeEdges = useGraphStore((s) => s.edges);
  const setGraph = useGraphStore((s) => s.setGraph);
  const setSelectedEntity = useGraphStore((s) => s.setSelectedEntity);
  const selectedEntityId = useGraphStore((s) => s.selectedEntityId);

  const [rfNodes, setRfNodes, onNodesChange] = useNodesState<Node>([]);
  const [rfEdges, setRfEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [searchQ, setSearchQ] = useState("");
  const [hoverId, setHoverId] = useState<string | null>(null);
  const [direction, setDirection] = useState<"LR" | "TB">("LR");
  const { fitView } = useReactFlow();

  useEffect(() => {
    apiClient
      .get<{ nodes: typeof storeNodes; edges: typeof storeEdges }>("/v1/graph")
      .then(({ nodes, edges }) => setGraph(nodes, edges))
      .catch(() => {});
  }, []);

  useEffect(() => {
    const laid = layoutGraph(
      storeNodes as unknown as Node[],
      storeEdges as unknown as Edge[],
      direction
    );
    setRfNodes(laid);
    setRfEdges(storeEdges as unknown as Edge[]);
    setTimeout(() => fitView({ padding: 0.25, duration: 600 }), 80);
  }, [storeNodes, storeEdges, direction]);

  const neighbors = useMemo(() => {
    const m: Record<string, Set<string>> = {};
    rfEdges.forEach((e) => {
      (m[e.source] ||= new Set()).add(e.target);
      (m[e.target] ||= new Set()).add(e.source);
    });
    return m;
  }, [rfEdges]);

  const visualNodes = useMemo<Node[]>(() => {
    const focusId = hoverId ?? selectedEntityId;
    return rfNodes.map((n) => {
      const isFocus = focusId && (n.id === focusId || neighbors[focusId]?.has(n.id));
      const dim = focusId ? !isFocus : false;
      return {
        ...n,
        style: {
          ...(n.style ?? {}),
          opacity: dim ? 0.18 : 1,
          transition: "opacity 0.2s, filter 0.2s",
          filter: dim ? "saturate(0)" : "none",
        },
      };
    });
  }, [rfNodes, hoverId, selectedEntityId, neighbors]);

  const visualEdges = useMemo<Edge[]>(() => {
    const focusId = hoverId ?? selectedEntityId;
    return rfEdges.map((e) => {
      const touchesFocus =
        focusId && (e.source === focusId || e.target === focusId);
      const dim = focusId ? !touchesFocus : false;
      return {
        ...e,
        type: "relationEdge",
        style: {
          ...(e.style ?? {}),
          opacity: dim ? 0.06 : 1,
          transition: "opacity 0.2s",
        },
        animated: !!touchesFocus,
      };
    });
  }, [rfEdges, hoverId, selectedEntityId]);

  const onNodeClick: NodeMouseHandler = useCallback(
    (_e, node) => {
      setSelectedEntity(node.id === selectedEntityId ? null : node.id);
    },
    [selectedEntityId, setSelectedEntity]
  );

  const handleSearch = async (q: string) => {
    setSearchQ(q);
    if (!q) return;
    try {
      const result = await apiClient.get<{ results: Array<{ id: string }> }>(
        "/v1/graph/search",
        { q }
      );
      const first = result.results[0]?.id;
      if (first) setSelectedEntity(first);
    } catch {
      /* ignore */
    }
  };

  const isEmpty = rfNodes.length === 0;

  return (
    <div
      className="w-full h-full relative"
      style={{ background: "var(--graph-bg)" }}
    >
      <ArrowDefs />

      {/* Floating toolbar */}
      <div className="absolute top-3 left-3 right-3 z-10 flex items-center gap-2">
        {/* Search */}
        <div
          className="flex items-center gap-2 flex-1 max-w-xs px-2.5 py-1.5 rounded-lg"
          style={{
            background: "var(--bg-elevated)",
            border: "1px solid var(--border-default)",
            backdropFilter: "blur(12px)",
          }}
        >
          <Search size={12} style={{ color: "var(--text-muted)", flexShrink: 0 }} />
          <input
            className="flex-1 bg-transparent text-xs focus:outline-none"
            style={{ color: "var(--text-primary)" }}
            placeholder="Search entities…"
            value={searchQ}
            onChange={(e) => handleSearch(e.target.value)}
          />
        </div>

        {/* Layout toggle */}
        <button
          onClick={() => setDirection((d) => (d === "LR" ? "TB" : "LR"))}
          className="p-1.5 rounded-lg transition-all duration-150"
          style={{
            background: "var(--bg-elevated)",
            border: "1px solid var(--border-default)",
            color: "var(--text-muted)",
            backdropFilter: "blur(12px)",
          }}
          title={`Layout: ${direction === "LR" ? "horizontal" : "vertical"}`}
        >
          <LayoutGrid size={14} />
        </button>

        {/* Fit view */}
        <button
          onClick={() => fitView({ padding: 0.25, duration: 600 })}
          className="p-1.5 rounded-lg transition-all duration-150"
          style={{
            background: "var(--bg-elevated)",
            border: "1px solid var(--border-default)",
            color: "var(--text-muted)",
            backdropFilter: "blur(12px)",
          }}
          title="Fit view"
        >
          <Maximize2 size={14} />
        </button>
      </div>

      {/* Entity type legend */}
      {!isEmpty && (
        <div
          className="absolute bottom-3 left-3 z-10 flex flex-col gap-1.5 px-2.5 py-2 rounded-lg"
          style={{
            background: "var(--bg-elevated)",
            border: "1px solid var(--border-default)",
            backdropFilter: "blur(12px)",
          }}
        >
          {Object.entries(TYPE_COLORS).filter(([type]) =>
            rfNodes.some((n) => (n.data as { entityType?: string })?.entityType === type)
          ).map(([type, color]) => (
            <div key={type} className="flex items-center gap-1.5">
              <div
                className="w-1.5 h-1.5 rounded-full"
                style={{ background: color, boxShadow: `0 0 4px ${color}80` }}
              />
              <span className="text-[10px]" style={{ color: "rgba(148,163,184,0.6)" }}>{type}</span>
            </div>
          ))}
        </div>
      )}

      {isEmpty ? (
        <div
          className="flex flex-col items-center justify-center h-full gap-3"
          style={{ color: "rgba(100,116,139,0.5)" }}
        >
          <svg width="40" height="40" viewBox="0 0 40 40" fill="none" opacity="0.3">
            <circle cx="20" cy="20" r="4" fill="#22d3ee" />
            <circle cx="20" cy="20" r="14" stroke="#22d3ee" strokeWidth="1" />
            <circle cx="20" cy="6"  r="2" fill="#818cf8" />
            <circle cx="34" cy="20" r="2" fill="#22d3ee" />
            <circle cx="20" cy="34" r="2" fill="#818cf8" />
            <circle cx="6"  cy="20" r="2" fill="#22d3ee" />
          </svg>
          <p className="text-sm">Knowledge graph — entities appear as memories are stored</p>
        </div>
      ) : (
        <ReactFlow
          nodes={visualNodes}
          edges={visualEdges}
          nodeTypes={nodeTypes}
          edgeTypes={edgeTypes}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onNodeClick={onNodeClick}
          onNodeMouseEnter={(_e, n) => setHoverId(n.id)}
          onNodeMouseLeave={() => setHoverId(null)}
          fitView
          minZoom={0.15}
          maxZoom={3}
          proOptions={{ hideAttribution: true }}
          style={{ background: "var(--graph-bg)" }}
        >
          <Background
            variant={BackgroundVariant.Dots}
            color="var(--graph-dot)"
            gap={24}
            size={1}
          />
          <Controls
            className="graph-controls"
            showInteractive={false}
          />
          <MiniMap
            nodeColor={(n) =>
              TYPE_COLORS[(n.data as { entityType?: string })?.entityType ?? ""] ?? "#64748b"
            }
            style={{
              background: "var(--bg-elevated)",
              border: "1px solid var(--border-default)",
              backdropFilter: "blur(12px)",
              borderRadius: "10px",
            }}
            maskColor="var(--minimap-mask)"
            pannable
            zoomable
          />
        </ReactFlow>
      )}

      {selectedEntityId && (
        <NodeTooltip
          entityId={selectedEntityId}
          onClose={() => setSelectedEntity(null)}
        />
      )}
    </div>
  );
}

export default function GraphCanvas() {
  return (
    <ReactFlowProvider>
      <InnerGraph />
    </ReactFlowProvider>
  );
}
