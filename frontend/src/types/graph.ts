/**
 * types/graph.ts — TypeScript types for knowledge graph data.
 *
 * These types mirror backend/app/graph/models.py.
 * ReactFlowNode and ReactFlowEdge extend the base @xyflow/react types
 * with strongly-typed `data` payloads.
 */

/** Entity type categories — must match EntityType enum in backend. */
export type EntityType =
  | "Person"
  | "Location"
  | "Preference"
  | "Event"
  | "Concept"
  | "Health"
  | "Object"
  | "Other";

/** Data payload inside a React Flow entity node. */
export interface EntityNodeData {
  label: string;          // canonical_name
  entityType: EntityType;
  memoryCount: number;
  createdAt: string;      // ISO 8601
}

/** Data payload inside a React Flow relationship edge. */
export interface RelationEdgeData {
  valid: boolean;         // false = soft-deleted (conflict-resolved, shown as dashed)
  sourceMemoryId: string;
  createdAt: string;
}

/** React Flow–compatible node shape returned by GET /v1/graph. */
export interface ReactFlowNode {
  id: string;
  type: "entityNode";
  position: { x: number; y: number };
  data: EntityNodeData;
  className?: string;     // "animate-node-enter" for 400ms on new nodes
  selected?: boolean;
}

/** React Flow–compatible edge shape returned by GET /v1/graph. */
export interface ReactFlowEdge {
  id: string;
  source: string;
  target: string;
  label: string;
  type: "relationEdge";
  animated: boolean;      // true for 3s after creation
  data: RelationEdgeData;
}

/** Incremental graph update payload from `graph_delta` SSE event. */
export interface GraphDelta {
  nodesAdded: ReactFlowNode[];
  edgesAdded: ReactFlowEdge[];
  nodesUpdated: ReactFlowNode[];
}

/** Response from GET /v1/graph/entity/{id} (subgraph + memories). */
export interface EntitySubgraph {
  entity: ReactFlowNode;
  neighbours: ReactFlowNode[];
  edges: ReactFlowEdge[];
  memories: Array<{ id: string; text: string; createdAt: string }>;
}
