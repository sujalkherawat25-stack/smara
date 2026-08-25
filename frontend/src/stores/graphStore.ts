import { create } from "zustand";
import type { ReactFlowNode, ReactFlowEdge } from "@/types/graph";

interface GraphState {
  nodes: ReactFlowNode[];
  edges: ReactFlowEdge[];
  selectedEntityId: string | null;
  setGraph: (nodes: ReactFlowNode[], edges: ReactFlowEdge[]) => void;
  addNodes: (newNodes: ReactFlowNode[]) => void;
  addEdges: (newEdges: ReactFlowEdge[]) => void;
  updateNode: (updated: ReactFlowNode) => void;
  setSelectedEntity: (id: string | null) => void;
}

export const useGraphStore = create<GraphState>((set, get) => ({
  nodes: [],
  edges: [],
  selectedEntityId: null,

  setGraph: (nodes, edges) => set({ nodes, edges }),

  addNodes: (newNodes) => {
    const existing = new Set(get().nodes.map((n) => n.id));
    const toAdd = newNodes
      .filter((n) => !existing.has(n.id))
      .map((n) => ({ ...n, className: "animate-node-enter" }));
    if (toAdd.length === 0) return;
    set((s) => ({ nodes: [...s.nodes, ...toAdd] }));
    setTimeout(() => {
      const ids = new Set(toAdd.map((n) => n.id));
      set((s) => ({
        nodes: s.nodes.map((n) =>
          ids.has(n.id) ? { ...n, className: undefined } : n
        ),
      }));
    }, 400);
  },

  addEdges: (newEdges) => {
    const existing = new Set(get().edges.map((e) => e.id));
    const toAdd = newEdges
      .filter((e) => !existing.has(e.id))
      .map((e) => ({ ...e, animated: true }));
    if (toAdd.length === 0) return;
    set((s) => ({ edges: [...s.edges, ...toAdd] }));
    setTimeout(() => {
      const ids = new Set(toAdd.map((e) => e.id));
      set((s) => ({
        edges: s.edges.map((e) =>
          ids.has(e.id) ? { ...e, animated: false } : e
        ),
      }));
    }, 3000);
  },

  updateNode: (updated) =>
    set((s) => ({
      nodes: s.nodes.map((n) => (n.id === updated.id ? updated : n)),
    })),

  setSelectedEntity: (id) => set({ selectedEntityId: id }),
}));
