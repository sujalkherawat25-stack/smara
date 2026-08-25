/**
 * types/memory.ts — TypeScript types for memory pipeline data.
 *
 * These types mirror the Pydantic models in backend/app/memory/models.py
 * and backend/app/retrieval/models.py. Keep in sync when backend models change.
 */

export type MemoryOperation = "ADD" | "UPDATE" | "DELETE" | "NOOP";

/** A downloadable artifact produced by a tool (e.g. generate_pdf → .pdf). */
export interface MessageDownload {
  url: string;        // absolute https://… URL
  filename: string;
  kind: string;       // e.g. "document"
}

/** An inline Mermaid diagram produced by create_diagram. */
export interface MessageDiagram {
  title: string;
  mermaid: string;    // Mermaid source, rendered client-side
  kind: string;       // e.g. "mindmap", "flowchart"
}

/** A workflow Smara learned from repeated successful execution. */
export interface MessageSkillProposal {
  id: string;
  name: string;
  description: string;
  tools: string[];
  status: "pending" | "approved" | "disabled";
}

/** A single conversation turn (user or assistant). */
export interface ConversationMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  timestamp: string;          // ISO 8601
  retrievalTrace?: RetrievalTrace;  // Only set on assistant messages, after streaming completes
  download?: MessageDownload;       // Set when a tool produced a downloadable file this turn
  diagram?: MessageDiagram;         // Set when create_diagram rendered a diagram this turn
  skillProposal?: MessageSkillProposal;
  /** URLs that were actually fetched / cited by tools during this turn.
   *  Empty / undefined when no external source was consulted. Renders as a
   *  Perplexity-style "Sources" list under the assistant reply. */
  sources?: string[];
}

/** An extracted candidate memory awaiting user approval. */
export interface PendingMemory {
  id: string;
  text: string;
  sourceMessages: string[];   // The raw message texts it was extracted from
  conversationId: string;
  userId: string;
  extractedAt: string;        // ISO 8601
}

/** An approved memory written to Qdrant (and triggering Neo4j graph update). */
export interface Memory {
  id: string;
  text: string;
  userId: string;
  conversationId: string;
  operation: MemoryOperation;
  vectorId: string;
  createdAt: string;
  updatedAt: string | null;
  importance?: number;
  /** Computed server-side at read time for display only — never persisted,
   *  never used for retrieval ranking (that's computed fresh in fused_search). */
  decayFactor?: number;
  /** Set when a contradiction handler decided a newer memory replaces this
   *  one. The memory still exists and is still retrievable (decayed, not
   *  deleted) — this just tells the UI to mark it as stale. */
  supersededBy?: string | null;
}

/** A single memory result with per-signal scores. */
export interface RetrievalResult {
  memoryId: string;
  text: string;
  semanticScore: number | null;
  entityScore: number | null;
  fusedScore: number;
  sourceSignals: string[];    // e.g. ["semantic", "entity"]
  entityPath: string[];       // Entity names traversed (empty for pure semantic)
  used: boolean;              // True if included in agent prompt
}

/** Complete retrieval record for one agent response. Serialised as SSE frame. */
export interface RetrievalTrace {
  query: string;
  candidatesChecked: number;
  results: RetrievalResult[];
  latencyMs: number;
  memoriesUsed: number;       // Count where used=true
}

/** Stats response from GET /v1/stats. */
export interface Stats {
  totalMemories: number;
  totalEntities: number;
  totalRelationships: number;
  graphDensity: number;
  memoryHitRate: number;
  avgRetrievalLatencyMs: number;
  tokenSavingsPct: number;
  memoriesThisSession: number;
}

/** Benchmark result from POST /v1/benchmark. */
export interface BenchmarkArm {
  response: string;
  tokensUsed: number;
  latencyMs: number;
  memoriesRetrieved?: number;
}

export interface BenchmarkResult {
  id: string;
  mem0g: BenchmarkArm;
  fullContext: BenchmarkArm;
  tokenSavingsPct: number;
  createdAt: string;
}
