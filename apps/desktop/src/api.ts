import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import type { ChatEvent, ConnectionState, LocalCredentialSummary, RemoteStatus, TaskSummary } from "./types";

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
  }) => invoke<ConnectionState>("save_settings", { settings }),
  checkConnection: (apiUrl: string) => invoke<RemoteStatus>("check_connection", { apiUrl }),
  login: (apiUrl: string, webUrl: string) => invoke<string>("login_cli", { apiUrl, webUrl }),
  pair: (args: {
    api_url: string;
    code: string;
    allowed_roots: string[];
    terminal_allowlist: string[];
    browser_domains: string[];
  }) => invoke<ConnectionState>("pair_desktop", { args }),
  start: () => invoke<ConnectionState>("start_executor"),
  stop: () => invoke<ConnectionState>("stop_executor"),
  pause: () => invoke<ConnectionState>("pause_executor"),
  resume: () => invoke<ConnectionState>("resume_executor"),
  revoke: () => invoke<ConnectionState>("revoke_executor"),
  log: () => invoke<string>("read_log"),
  tasks: () => invoke<TaskSummary[]>("load_tasks"),
  openWeb: () => invoke<void>("open_web"),
  credentials: () => invoke<LocalCredentialSummary[]>("list_local_credentials"),
  saveCredential: (name: string, provider: string, secret: string) => invoke<LocalCredentialSummary[]>("save_local_credential", { name, provider, secret }),
  deleteCredential: (name: string) => invoke<LocalCredentialSummary[]>("delete_local_credential", { name }),
  streamChat: (args: { api_url: string; workspace: string; model_profile: string; message: string; conversation_id: string }) =>
    invoke<void>("stream_chat", { args }),
  onChatEvent: (handler: (event: ChatEvent) => void): Promise<UnlistenFn> =>
    listen<ChatEvent>("smara-chat-event", (event) => handler(event.payload)),
};
