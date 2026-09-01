import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import type { ChatEvent, ConnectionState, LocalConnectorSummary, LocalCredentialSummary, LocalModelProfile, RemoteStatus, TaskDetail, TaskSummary } from "./types";

export const isNativeDesktop = typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

export const desktop = {
  connection: () => invoke<ConnectionState>("load_connection"),
  saveSettings: (settings: {
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
};
