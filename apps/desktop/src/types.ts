export type Screen = "chat" | "activity" | "settings";

export interface ConnectionState {
  runtime_mode: "local" | "cloud";
  api_url: string;
  web_url: string;
  workspace: string;
  model_profile: string;
  paired: boolean;
  executor_id: string | null;
  capabilities: string[];
  allowed_roots: string[];
  terminal_allowlist: string[];
  browser_domains: string[];
  auto_approve_safe: boolean;
  approval_mode: "ask" | "auto";
  paused: boolean;
  running: boolean;
  pid: number | null;
  log_path: string;
  has_cli_token: boolean;
  last_error: string | null;
}

export interface LocalCredentialSummary {
  name: string;
  provider: string;
  updated_at: string;
}

export interface LocalConnectorSummary {
  provider: string;
  operation: string;
  credential_alias: string;
  auth_mode: string;
  risk: string;
  scopes: string[];
  max_results: number;
  max_requests_per_run: number;
  credential_configured: boolean;
}

export interface LocalModelProfile {
  id: string;
  label: string;
  provider: string;
  base_url: string;
  model: string;
  credential_name: string;
  auth_header: string;
  updated_at: string;
}

export interface RemoteStatus {
  ok: boolean;
  api_url: string;
  detail: string;
}

export interface TaskSummary {
  id: string;
  title: string;
  objective: string;
  status: string;
  approval_mode?: "hosted" | "desktop";
  local_approval_mode?: "ask" | "auto";
  updated_at?: string;
  created_at?: string;
  result?: string | null;
}

export interface TaskDetail {
  task: TaskSummary;
  steps: Array<Record<string, unknown>>;
  events: Array<{ id?: string; type?: string; payload?: string; created_at?: string }>;
  artifacts: Array<{ id?: string; kind?: string; name?: string; uri?: string; sha256?: string | null; content?: string | null; created_at?: string }>;
}

export interface ChatEvent {
  type?: string;
  text?: string;
  phase?: string;
  label?: string;
  name?: string;
  ok?: boolean;
  preview?: string;
  message?: string;
  kind?: string;
  recoverable?: boolean;
  total_ms?: number;
  tools_used?: number;
  task_id?: string;
  task_ids?: string[];
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
  pending?: boolean;
  failed?: boolean;
  error?: string;
}

export interface FilePreview {
  file_name: string;
  full_path: string;
  size_bytes: number;
  extension: string;
  is_text: boolean;
  preview_content: string;
}

export interface ASTSymbolInspection {
  name: string;
  kind?: string;
  file?: string;
  line_start?: number;
  line_end?: number;
  docstring?: string;
  defined_methods?: Array<{ name: string; line: number; signature: string }>;
  called_by?: Array<{ caller_file: string; caller_line: number; caller_name: string }>;
  blast_radius?: {
    symbol?: string;
    direct_callers?: number;
    affected_files?: string[];
    risk_level?: string;
    total_impact?: number;
  };
  error?: string;
}

export interface TestFailureItem {
  test_id: string;
  file_path: string;
  line_number: number | null;
  assertion_error: string;
  stack_trace: string;
}

export interface TestSuiteResultData {
  success: boolean;
  total: number;
  passed: number;
  failed: number;
  errors: number;
  skipped: number;
  duration_seconds: number;
  failures: TestFailureItem[];
  raw_output: string;
}

export interface AutoFixResultData {
  status: "healed" | "already_passing" | "unresolved_rollback";
  message: string;
  initial_tests?: TestSuiteResultData;
  final_tests?: TestSuiteResultData;
  iterations_count: number;
  iterations_log?: Array<Record<string, any>>;
  session_summary?: {
    session_id: string;
    files: Array<{ file: string; additions: number; deletions: number; diff: string }>;
  };
  rolled_back_files?: string[];
  duration_seconds: number;
}

export interface GitStatusData {
  is_repo: boolean;
  branch: string;
  is_clean: boolean;
  staged_files: string[];
  unstaged_files: string[];
  untracked_files: string[];
  conflicts: string[];
  total_changes: number;
  raw_diff?: string;
}

export interface GitCommitData {
  commit_hash: string;
  short_hash: string;
  author: string;
  date: string;
  message: string;
}

export interface GitSmartCommitData {
  title: string;
  description: string;
  type: string;
  scope?: string;
}

export interface GitConflictData {
  file: string;
  path: string;
}

export interface SearchResultItem {
  file_path: string;
  symbol_name: string;
  kind: string;
  start_line: number;
  end_line: number;
  score: number;
  percentage: number;
  match_type: "hybrid" | "semantic" | "lexical";
  docstring: string;
  code_snippet: string;
}

export interface SemanticIndexStats {
  indexed_files: number;
  skipped_files: number;
  total_chunks_added: number;
}

export interface ActivityItem {
  id: string;
  tone: "green" | "blue" | "amber" | "red" | "muted";
  label: string;
  detail?: string;
}

export interface BrowserStepResultData {
  step_index: number;
  action: string;
  target: string;
  status: "passed" | "failed" | "info";
  duration_ms: number;
  details: string;
  screenshot_base64?: string;
  dom_snapshot?: string;
}

export interface E2ESuiteResultData {
  suite_name: string;
  success: boolean;
  passed_count: number;
  failed_count: number;
  total_duration_ms: number;
  steps: BrowserStepResultData[];
  failure_reason?: string;
  suggested_fix?: string;
}

export interface WebScrapeData {
  success: boolean;
  url: string;
  title?: string;
  headings?: string[];
  content_snippet?: string;
  dom_length?: number;
  duration_ms: number;
  error?: string;
}

export interface BrowserScreenshotData {
  success: boolean;
  url: string;
  file_path?: string;
  file_size?: number;
  data_url: string;
}

export interface PlaneStatusData {
  name: string;
  plane_type: string;
  status: "active" | "connected" | "standby" | "unconfigured";
  endpoint: string;
  items_count: number;
  details: string;
}

export interface DualPlaneStatusData {
  plane_1_local: PlaneStatusData;
  plane_2_continuum: PlaneStatusData;
  bridge_active: boolean;
  last_sync_time: string | null;
  total_memories_synced: number;
}

export interface DualPlaneRecallData {
  query: string;
  local_symbols: SearchResultItem[];
  continuum_memories: string[];
  fused_context: string;
  retrieval_ms: number;
}

export interface ADRData {
  id: string;
  title: string;
  date: string;
  status: "Accepted" | "Proposed" | "Deprecated" | "Superseded";
  context: string;
  decision: string;
  consequences: string;
  symbols_affected: string[];
  source?: string;
}

export interface SymbolEvolutionData {
  symbol_name: string;
  file_path: string;
  timestamp: string;
  change_type: "added" | "removed" | "signature_modified" | "doc_updated";
  diff_description: string;
  old_signature?: string | null;
  new_signature?: string | null;
  commit_hash?: string | null;
}

export interface CodingConventionsData {
  workspace_name: string;
  analyzed_files_count: number;
  async_percentage: number;
  type_hint_coverage: number;
  test_framework: string;
  naming_conventions: Record<string, string>;
  key_patterns: string[];
  last_updated: string;
}

export interface SwarmMessageData {
  from_role: "architect" | "implementer" | "verifier" | "auditor";
  to_role: "architect" | "implementer" | "verifier" | "auditor";
  action: string;
  payload: Record<string, any>;
  timestamp: string;
}

export interface ArchitectPlanData {
  objective: string;
  target_symbols: string[];
  blast_radius: string[];
  adrs_consulted: string[];
  conventions_noted: string[];
  steps: string[];
  risk_level: "LOW" | "MEDIUM" | "HIGH";
}

export interface SwarmTaskResultData {
  session_id: string;
  objective: string;
  status: "SUCCESS" | "FAILED" | "HEALED";
  duration_ms: number;
  architect_plan: ArchitectPlanData | null;
  files_modified: string[];
  tests_run: number;
  tests_passed: number;
  healing_applied: boolean;
  audit_passed: boolean;
  commit_message: string | null;
  inter_agent_messages: SwarmMessageData[];
}

export interface TaskMemoryStoreData {
  target: "memory" | "user";
  entries: string[];
}

export interface TaskMemoryActionResult {
  status: "success" | "error" | "noop";
  message: string;
  entry_count?: number;
  updated_entry?: string;
  removed_entry?: string;
}

export interface TaskMemorySearchItem {
  store: string;
  content: string;
  relevance: number;
}

export interface TaskMemorySnapshot {
  snapshot: string;
}

export interface ProgressiveSkillItem {
  name: string;
  description: string;
  version: string;
  tags: string[];
  source: string;
  skill_dir: string;
}

export interface ProgressiveSkillDetail {
  status: string;
  skill: string;
  metadata?: {
    name?: string;
    description?: string;
    version?: string;
    tags?: string[];
    source?: string;
  };
  instructions?: string;
  available_assets?: string[];
  file?: string;
  content?: string;
  message?: string;
}

export type DAGNodeStatus = "PENDING" | "READY" | "RUNNING" | "COMPLETED" | "FAILED" | "BLOCKED" | "SKIPPED";

export interface DAGNodeData {
  id: string;
  title: string;
  capability: string;
  payload: Record<string, any>;
  depends_on: string[];
  status: DAGNodeStatus;
  result?: any;
  error?: string | null;
  duration_ms: number;
  retries: number;
  max_retries: number;
}

export interface DAGWorkflowData {
  id: string;
  title: string;
  is_paused: boolean;
  nodes: DAGNodeData[];
  ascii_view?: string;
}

export interface SubagentRoleInfo {
  id: string;
  name: string;
  description: string;
}

export interface SubagentRolesData {
  roles: SubagentRoleInfo[];
  blocked_tools: string[];
  safe_tools: string[];
}

export interface SubagentDelegationData {
  task_id: string;
  goal: string;
  status: "SUCCESS" | "FAILED" | "TIMEOUT";
  summary: string;
  trace_steps: number;
  duration_ms: number;
  tools_used: string[];
  error?: string | null;
}



