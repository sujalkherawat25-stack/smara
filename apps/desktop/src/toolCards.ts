import type { ViewItem } from "./nativeView";

/** One-line summaries for tool rows, in the style of Claude Code's tool lines. Message rows are not tool rows. */
export type ToolSummary = { verb: string; target: string; state: "running" | "ok" | "failed" | "done"; duration: string; line: string };
export type TranscriptSegment =
  | { type: "item"; item: ViewItem }
  | { type: "group"; key: string; items: ViewItem[]; running: boolean };

const MESSAGE_KINDS = new Set(["assistant", "user"]);

export function isToolItem(item: ViewItem): boolean {
  return !MESSAGE_KINDS.has(item.kind);
}

function firstLine(text: string, limit: number): string {
  const line = text.split("\n").find(part => part.trim())?.trim() ?? "";
  return line.length > limit ? `${line.slice(0, limit - 1)}…` : line;
}

function durationText(ms: number | undefined): string {
  if (ms === undefined || !Number.isFinite(ms)) return "";
  return ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)} s`;
}

export function toolSummary(item: ViewItem): ToolSummary {
  const duration = durationText(item.durationMs);
  if (item.kind === "commandExecution") {
    const target = firstLine(item.command ?? "command", 80);
    const failed = item.status === "failed" || (item.exitCode !== undefined && item.exitCode !== 0);
    const state: ToolSummary["state"] = item.status === "inProgress" ? "running" : failed ? "failed" : item.exitCode === 0 ? "ok" : "done";
    const mark = state === "running" ? "running…" : state === "failed" ? `✗ exit ${item.exitCode ?? "?"}` : state === "ok" ? "✓" : "done";
    // "Ran npm test · ✓ 3.2 s": the verb and target, then the status and duration.
    const tail = mark ? `${mark}${duration ? ` ${duration}` : ""}` : duration;
    return { verb: "Ran", target, state, duration, line: [`Ran ${target}`, tail].filter(Boolean).join(" · ") };
  }
  const state: ToolSummary["state"] = item.status === "inProgress" ? "running" : item.status === "failed" ? "failed" : "done";
  const verbs: Record<string, string> = { fileChange: "Edited", webSearch: "Searched the web", browserStep: "Browsed", mcpToolCall: "Used a tool" };
  const verb = verbs[item.kind] ?? (item.kind.replace(/([A-Z])/g, " $1").trim() || "Tool");
  const target = firstLine(item.text, 80);
  const label = state === "running" ? "running…" : state === "failed" ? "✗ failed" : "";
  return { verb, target, state, duration, line: [verb, target, label, duration].filter(Boolean).join(" · ") };
}

/**
 * Consecutive tool rows collapse into one "N steps" group once there are two or more. The group is open while any
 * step is running; a single tool row stays a plain row.
 */
export function groupTranscript(items: ViewItem[]): TranscriptSegment[] {
  const segments: TranscriptSegment[] = [];
  let run: ViewItem[] = [];
  const flush = () => {
    if (run.length >= 2) {
      segments.push({ type: "group", key: run[0].id, items: run, running: run.some(item => item.status === "inProgress") });
    } else {
      for (const item of run) segments.push({ type: "item", item });
    }
    run = [];
  };
  for (const item of items) {
    if (isToolItem(item)) run.push(item);
    else { flush(); segments.push({ type: "item", item }); }
  }
  flush();
  return segments;
}
