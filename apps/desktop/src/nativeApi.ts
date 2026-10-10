import { invoke } from "@tauri-apps/api/core";
import type { LocalModelProfile, LocalCredentialSummary } from "./types";
export type Project = { workspace: string; label: string };
export type NativeSetup = {
  preferences: { projects: Project[]; workspace: string; active_model: string; search_provider: string };
  profiles: LocalModelProfile[];
  credentials: LocalCredentialSummary[];
  search: { provider: string; configured: boolean; network_tested: boolean; detail: string; credential_alias?: string; endpoint?: string };
};
export type NativeProbe = {
  kind: "model" | "search"; ok: boolean; model?: string; elapsed_ms?: number;
  provider?: string; query?: string; result_count?: number;
};
export const nativeSettings = (operation = "bootstrap", values: Record<string, unknown> = {}) =>
  invoke<NativeSetup>("native_manage", { request: { operation, ...values } });
export const nativeProbe = (operation: "test_model" | "test_search", values: Record<string, unknown> = {}) =>
  invoke<NativeProbe>("native_manage", { request: { operation, ...values } });
export function sameWorkspace(a: string, b: string): boolean {
  const normalize = (path: string) => {
    const value = path.replace(/^\\\\\?\\/, "").replace(/\\/g, "/").replace(/\/+$/, "");
    return /^[a-z]:\//i.test(value) || value.startsWith("//") ? value.toLowerCase() : value;
  };
  return !!a && !!b && normalize(a) === normalize(b);
}
