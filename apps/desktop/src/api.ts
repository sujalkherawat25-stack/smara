import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import type { ADRData, ASTSymbolInspection, AutoFixResultData, BrowserScreenshotData, BrowserStepResultData, ChatEvent, CodingConventionsData, ConnectionState, DualPlaneRecallData, DualPlaneStatusData, E2ESuiteResultData, FilePreview, GitCommitData, GitConflictData, GitSmartCommitData, GitStatusData, LocalConnectorSummary, LocalCredentialSummary, LocalModelProfile, RemoteStatus, SearchResultItem, SemanticIndexStats, SwarmTaskResultData, SymbolEvolutionData, TaskDetail, TaskSummary, TestSuiteResultData, WebScrapeData } from "./types";

export const isNativeDesktop = typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

export const desktop = {
  connection: () => invoke<ConnectionState>("load_connection"),
  saveSettings: (settings: {
    runtime_mode: "local" | "cloud";
    api_url: string;
    web_url: string;
    workspace: string;
    model_profile: string;
    allowed_roots: string[];
    terminal_allowlist: string[];
    browser_domains: string[];
    auto_approve_safe: boolean;
    approval_mode: "ask" | "auto";
  }) => invoke<ConnectionState>("save_settings", { settings }),
  checkConnection: (apiUrl: string) => invoke<RemoteStatus>("check_connection", { apiUrl }),
  login: (apiUrl: string, webUrl: string) => invoke<string>("login_cli", { apiUrl, webUrl }),
  pair: (args: {
    runtime_mode: "local" | "cloud";
    api_url: string;
    code: string;
    allowed_roots: string[];
    terminal_allowlist: string[];
    browser_domains: string[];
    auto_approve_safe: boolean;
    approval_mode: "ask" | "auto";
  }) => invoke<ConnectionState>("pair_desktop", { args }),
  start: () => invoke<ConnectionState>("start_executor"),
  stop: () => invoke<ConnectionState>("stop_executor"),
  pause: () => invoke<ConnectionState>("pause_executor"),
  resume: () => invoke<ConnectionState>("resume_executor"),
  revoke: () => invoke<ConnectionState>("revoke_executor"),
  log: () => invoke<string>("read_log"),
  tasks: () => invoke<TaskSummary[]>("load_tasks"),
  localTaskDetails: (taskId: string) => invoke<TaskDetail>("load_task_details", { taskId }),
  taskDetails: (taskId: string) => invoke<TaskDetail>("load_task_details", { taskId }),
  decideLocalTask: (taskId: string, approved: boolean) => invoke<void>("decide_local_task", { taskId, approved }),
  openWeb: () => invoke<void>("open_web"),
  credentials: () => invoke<LocalCredentialSummary[]>("list_local_credentials"),
  saveCredential: (name: string, provider: string, secret: string) => invoke<LocalCredentialSummary[]>("save_local_credential", { name, provider, secret }),
  deleteCredential: (name: string) => invoke<LocalCredentialSummary[]>("delete_local_credential", { name }),
  connectors: () => invoke<LocalConnectorSummary[]>("list_local_connectors"),
  revokeConnector: (provider: string) => invoke<LocalConnectorSummary[]>("revoke_local_connector", { provider }),
  modelProfiles: () => invoke<LocalModelProfile[]>("list_local_model_profiles"),
  saveModelProfile: (profile: { id: string; label: string; provider: string; base_url: string; model: string; api_key: string; auth_header?: string }) => invoke<LocalModelProfile[]>("save_local_model_profile", { profile }),
  deleteModelProfile: (id: string) => invoke<LocalModelProfile[]>("delete_local_model_profile", { id }),
  streamChat: (args: { api_url: string; workspace: string; model_profile: string; message: string; conversation_id: string }) =>
    invoke<void>("stream_chat", { args }),
  onChatEvent: (handler: (event: ChatEvent) => void): Promise<UnlistenFn> =>
    listen<ChatEvent>("smara-chat-event", (event) => handler(event.payload)),
  openFile: (path: string) => invoke<void>("open_file_in_default_app", { path }),
  revealFile: (path: string) => invoke<void>("reveal_file_in_explorer", { path }),
  readFilePreview: (path: string) => invoke<FilePreview>("read_file_preview", { path }),
  inspectAstGraph: (symbol: string) => invoke<ASTSymbolInspection>("inspect_ast_graph", { symbol }),
  runTestSuite: (filter?: string) => invoke<TestSuiteResultData>("run_test_suite", { filter: filter || null }),
  autoFixTests: (filter?: string) => invoke<AutoFixResultData>("auto_fix_tests", { filter: filter || null }),
  rollbackSnapshot: (sessionId: string) => invoke<string[]>("rollback_refactor_snapshot", { sessionId }),
  getGitStatus: () => invoke<GitStatusData>("get_git_status"),
  getGitBranches: () => invoke<string[]>("get_git_branches"),
  createGitBranch: (name: string) => invoke<string>("create_git_branch", { name }),
  switchGitBranch: (name: string) => invoke<string>("switch_git_branch", { name }),
  generateAiCommitMessage: () => invoke<GitSmartCommitData>("generate_ai_commit_message"),
  commitGitChanges: (message: string, stageAll: boolean = true) => invoke<string>("commit_git_changes", { message, stageAll }),
  getGitLog: (limit?: number) => invoke<GitCommitData[]>("get_git_log", { limit: limit || 15 }),
  detectGitConflicts: () => invoke<GitConflictData[]>("detect_git_conflicts"),
  resolveGitConflict: (filePath: string, strategy: string) => invoke<string>("resolve_git_conflict", { filePath, strategy }),
  semanticSearch: (query: string, limit?: number) => invoke<SearchResultItem[]>("semantic_search", { query, limit: limit || 8 }),
  rebuildSemanticIndex: (force: boolean = false) => invoke<SemanticIndexStats>("rebuild_semantic_index", { force }),
  scrapeWebPage: (url: string) => invoke<WebScrapeData>("scrape_web_page", { url }),
  captureBrowserScreenshot: (url: string) => invoke<BrowserScreenshotData>("capture_browser_screenshot", { url }),
  runBrowserE2E: (suiteName: string, steps: Array<Record<string, any>>) =>
    invoke<E2ESuiteResultData>("run_browser_e2e", { suiteName, stepsJson: JSON.stringify(steps) }),
  diagnoseBrowserUiComponent: (brokenText: string) => invoke<any>("diagnose_browser_ui_component", { brokenText }),
  getDualPlaneStatus: () => invoke<DualPlaneStatusData>("get_dual_plane_status"),
  syncDualPlaneMemory: (force: boolean = false) => invoke<any>("sync_dual_plane_memory", { force }),
  queryDualPlaneMemory: (query: string) => invoke<DualPlaneRecallData>("query_dual_plane_memory", { query }),
  listADRs: () => invoke<ADRData[]>("list_adrs"),
  createADR: (title: string, context: string, decision: string, consequences: string, symbolsAffected: string[] = []) =>
    invoke<ADRData>("create_adr", { title, context, decision, consequences, symbolsAffected }),
  getCodingConventions: () => invoke<CodingConventionsData>("get_coding_conventions"),
  getSymbolEvolution: (symbol: string) => invoke<SymbolEvolutionData[]>("get_symbol_evolution", { symbol }),
  runSwarmTask: (objective: string) => invoke<SwarmTaskResultData>("run_swarm_task", { objective }),
  getSwarmHistory: () => invoke<SwarmTaskResultData[]>("get_swarm_history"),
  getDynamicTools: () => invoke<any[]>("get_dynamic_tools"),
  runDynamicTool: (name: string, payload: any = {}) => invoke<any>("run_dynamic_tool", { name, payload }),
  synthesizeDynamicTool: (name: string, description: string, code: string, parameters: any = {}, samplePayload: any = {}) =>
    invoke<any>("synthesize_dynamic_tool", { name, description, code, parameters, samplePayload }),
  runGoalTask: (objective: string) => invoke<any>("run_goal_task", { objective }),
  getGoalSessions: () => invoke<any[]>("get_goal_sessions"),
  runDeepResearch: (topic: string) => invoke<any>("run_deep_research", { topic }),
  generatePrDraft: (intent?: string) => invoke<any>("generate_pr_draft", { intent: intent || null }),
  publishPrBranch: (draftTitle: string, branchName: string, commitMessage: string, bodyMarkdown: string) =>
    invoke<any>("publish_pr_branch", { draftTitle, branchName, commitMessage, bodyMarkdown }),
  runTerminalCommand: (command: string, cwd?: string) => invoke<any>("run_terminal_command", { command, cwd: cwd || null }),
  getFileGitDiff: (filePath: string) => invoke<any>("get_file_git_diff", { filePath }),
  listLearnedSkills: () => invoke<any[]>("list_learned_skills"),
  saveLearnedSkill: (name: string, description: string, triggers: string[], instructions: string) =>
    invoke<any>("save_learned_skill", { name, description, triggers, instructions }),
  deleteLearnedSkill: (name: string) => invoke<{ ok: boolean }>("delete_learned_skill", { name }),
  runGaiaBenchmark: (level?: string, count?: number) => invoke<any>("run_gaia_benchmark", { level: level || null, count: count || null }),
  runSweBenchmark: () => invoke<any>("run_swe_benchmark"),
  getBenchmarkScorecards: () => invoke<any>("get_benchmark_scorecards"),
  openBenchmarkReport: (path: string) => invoke<boolean>("open_benchmark_report", { path }),
};


