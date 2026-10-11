import type { ViewItem } from "./nativeView";

/** Read-only native command transcript. Never starts a second shell/runner. */
export default function NativeCommand({ item, expanded = false }: { item: ViewItem; expanded?: boolean }) {
  const state = item.status === "inProgress" ? "Running" : item.status === "declined" ? "Denied" : item.status === "failed" || (item.exitCode !== undefined && item.exitCode !== 0) ? "Failed" : item.status === "completed" ? "Finished" : "Status unknown";
  return <details className={`command-card ${state.toLowerCase().replaceAll(" ", "-")}`} open={expanded || state === "Running" || state === "Failed"}>
    <summary><span className="command-state">{state}</span><code>{item.command || "Command details unavailable"}</code><small>{item.exitCode !== undefined ? `Exit ${item.exitCode}` : state === "Running" ? "No exit code yet" : "Exit code unavailable"}{item.durationMs !== undefined && ` · ${(item.durationMs / 1000).toFixed(1)}s`}</small></summary>
    {item.cwd && <div className="command-cwd">Folder: {item.cwd}</div>}
    <pre aria-label="Command output">{item.text || (state === "Running" ? "Waiting for output…" : "No output recorded.")}</pre>
  </details>;
}
