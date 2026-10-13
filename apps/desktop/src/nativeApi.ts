import { invoke } from "@tauri-apps/api/core";
import type { LocalModelProfile, LocalCredentialSummary } from "./types";
export type ProjectOptions = { tools_enabled: boolean; workers_enabled: boolean; browser_origins: string[] };
export type Project = { workspace: string; label: string; options?: ProjectOptions };
export type NativeSetup = {
  preferences: { projects: Project[]; workspace: string; active_model: string; search_provider: string };
  profiles: LocalModelProfile[];
  credentials: LocalCredentialSummary[];
  search: { provider: string; configured: boolean; network_tested: boolean; detail: string; credential_alias?: string; endpoint?: string };
  memory?: MemoryStatus;
  skills_roots?: string[];
  computer?: { enabled: boolean; expires: number | null };
};
export type MemoryStatus = {
  enabled: boolean; user_id: string; scope: "project" | "personal"; configured: boolean;
  sdk_available: boolean; endpoint: string; network_tested: boolean; detail: string;
  writes: Record<string, number>; last_activity: { detail: string; updated: number } | null;
  level?: "disabled" | "unconfigured" | "ready" | "pending" | "warning";
};
export type NativeProbe = {
  kind: "model" | "search" | "memory"; ok: boolean; model?: string; elapsed_ms?: number; detail?: string;
  provider?: string; query?: string; result_count?: number;
};
export const nativeSettings = (operation = "bootstrap", values: Record<string, unknown> = {}) =>
  invoke<NativeSetup>("native_manage", { request: { operation, ...values } });
export const nativeProbe = (operation: "test_model" | "test_search" | "test_memory", values: Record<string, unknown> = {}) =>
  invoke<NativeProbe>("native_manage", { request: { operation, ...values } });
export function sameWorkspace(a: string, b: string): boolean {
  const normalize = (path: string) => {
    const value = path.replace(/^\\\\\?\\/, "").replace(/\\/g, "/").replace(/\/+$/, "");
    return /^[a-z]:\//i.test(value) || value.startsWith("//") ? value.toLowerCase() : value;
  };
  return !!a && !!b && normalize(a) === normalize(b);
}
