import { invoke } from "@tauri-apps/api/core";

export type GoalState = "queued" | "running" | "awaiting_approval" | "paused" | "blocked" | "ready_for_review" | "completed" | "stopped";
export type Goal = {
  id: string; state: GoalState; session_id: string | null; lease: string | null; updated: number;
  cycles: number; requests: number; token_estimate: number; detail: string;
  spec: { objective: string; success_criteria: string; workspace: string; profile_id: string; sandbox: string; origin_thread_id?: string; task_kind?: string; max_cycles: number; max_requests: number; max_token_estimate: number };
  checkpoint: { summary?: string; next_step?: string; plan?: string[]; completed_steps?: string[]; evidence?: string[]; outputs?: string[]; outcome?: { passed: boolean; elapsed_seconds: number; checks: { name: string; passed: boolean; detail: string }[]; sources: { url: string }[]; failed_pages: string[]; claims: { claim: string; url: string; quote: string; quote_found: boolean }[];
    metrics?: { citation_support: number | null; evidence_coverage: number | null };
    coverage?: { id: string; description: string; evidence_present: boolean }[];
    tests?: { command: string; passed: boolean; failure_excerpt?: string }[];
    outputs?: { path: string; sha256: string; bytes: number }[];
  } };
  events?: { seq: number; kind: string; created: number; body: { summary?: string; failure?: { stage: string; error_type: string; context?: string | null } } }[];
  approvals?: { id: string; method: string; params: Record<string, unknown> }[];
};
export type GoalList = { goals: Goal[]; current_goal?: Goal | null; service: { running?: boolean; heartbeat?: number; pid?: number; online?: boolean | null } };
export type TelegramStatus = { configured: boolean; paired: boolean; health?: { status?: string; detail?: string; last_poll?: number }; service?: { running?: boolean }; options?: { workspace?: string; profile_id?: string; sandbox?: string } };
export const goalRequest = <T,>(action: string, values: Record<string, unknown> = {}) =>
  invoke<T>("native_manage", { request: { operation: "goals", goal: { action, ...values } } });
export const goalLabel = (state: GoalState) => ({ queued: "Queued", running: "Working", awaiting_approval: "Needs your response", paused: "Paused", blocked: "Needs attention", ready_for_review: "Ready for review", completed: "Completed", stopped: "Stopped" })[state];
export const goalActive = (goal: Goal) => !!goal.lease;
