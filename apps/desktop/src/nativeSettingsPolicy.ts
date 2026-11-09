/**
 * Which settings operations change what the running native runtime was started with.
 * Must agree with `needs_runtime_restart` in src-tauri/src/native_runtime.rs (the Rust side is the authority).
 * Everything else runs while the chat stays connected.
 */
export type RunningProfile = { profileId: string; credentialName: string };

const RESTART_OPERATIONS = new Set(["select_model", "select_project", "save_project_options"]);

export function runtimeRestartNeeded(operation: string, values: Record<string, unknown>, running: RunningProfile | null): boolean {
  if (!running) return false;
  if (RESTART_OPERATIONS.has(operation)) return true;
  if (operation === "save_model") return (values.profile as { id?: unknown } | undefined)?.id === running.profileId;
  if (operation === "save_credential" || operation === "delete_credential") return values.name === running.credentialName;
  return false;
}
