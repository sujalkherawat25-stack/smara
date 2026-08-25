export type StageId =
  | 'pii_redaction'
  | 'redis_state'
  | 'fact_extraction'
  | 'cosine_dedup'
  | 'qdrant_vault'
  | 'neo4j_graph'
  | 'fused_retrieval';

export interface CodeSnippet {
  filename: string;
  filepath: string;
  language: string;
  code: string;
  description: string;
}

export interface MathFormula {
  title: string;
  latex: string;
  explanation: string;
}

export interface TransformationSample {
  inputLabel: string;
  inputJson: unknown;
  outputLabel: string;
  outputJson: unknown;
}

export interface SystemNode {
  id: StageId;
  stepNumber: number;
  title: string;
  subtitle: string;
  tagline: string;
  iconName: string;
  color: string;
  glowColor: string;
  position: [number, number, number];
  geometryType: 'hexagon_shield' | 'pulsing_core' | 'neural_sphere' | 'twin_gate' | 'matrix_vault' | 'graph_cluster' | 'prism_engine';
  purpose: string;
  deepExplanation: string[];
  codeSnippets: CodeSnippet[];
  mathFormulas: MathFormula[];
  dataTransformation: TransformationSample;
  keyMetrics: { label: string; value: string }[];
  storeTarget?: 'redis' | 'qdrant' | 'neo4j' | 'llm' | 'security';
}

export interface SimulationStepData {
  stageId: StageId;
  status: 'pending' | 'processing' | 'completed' | 'bypassed' | 'updated';
  statusMessage: string;
  durationMs: number;
  extractedData?: unknown;
}

export interface SimulationPreset {
  id: string;
  title: string;
  description: string;
  rawUserMessage: string;
  assistantReply: string;
  steps: Record<StageId, SimulationStepData>;
}

export interface CameraViewPreset {
  name: string;
  position: [number, number, number];
  target: [number, number, number];
}
