export type Screen = "chat" | "activity" | "settings";

export interface ConnectionState {
  api_url: string;
  workspace: string;
  model_profile: string;
  paired: boolean;
  executor_id: string | null;
  capabilities: string[];
  allowed_roots: string[];
  terminal_allowlist: string[];
  browser_domains: string[];
  paused: boolean;
  running: boolean;
  pid: number | null;
  log_path: string;
  has_cli_token: boolean;
  last_error: string | null;
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
  updated_at?: string;
  created_at?: string;
  result?: string | null;
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
  total_ms?: number;
  tools_used?: number;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
  pending?: boolean;
  failed?: boolean;
}

export interface ActivityItem {
  id: string;
  tone: "green" | "blue" | "amber" | "red" | "muted";
  label: string;
  detail?: string;
}
