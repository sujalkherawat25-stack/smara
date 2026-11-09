import { useState } from "react";
import type { ViewItem } from "../nativeView";
import NativeCommand from "../NativeCommand";
import { toolSummary } from "../toolCards";

const ICONS: Record<string, string> = { running: "⟳", ok: "✓", failed: "✗", done: "•" };
const SHOW_LINES = 2000;

/** One tool use as a single line. Click to see the command, its folder, exit code, and output. */
export function ToolCard({ item }: { item: ViewItem }) {
  const summary = toolSummary(item);
  // Failed rows open by default, so the reason is visible without a click.
  const [open, setOpen] = useState(summary.state === "failed");
  const [showAll, setShowAll] = useState(false);
  const lines = item.text.split("\n");
  const shown = showAll ? item.text : lines.slice(0, SHOW_LINES).join("\n");
  const truncated = !showAll && lines.length > SHOW_LINES;
  return (
    <div className="tool-card" data-state={summary.state}>
      <button className="tool-line" aria-expanded={open} onClick={() => setOpen(!open)}>
        <span className="tool-icon" aria-hidden="true">{ICONS[summary.state]}</span>
        <span>{summary.line}</span>
      </button>
      {open && <div className="tool-details">
        {item.kind === "commandExecution"
          ? <NativeCommand item={item} />
          : <div className="tool-output">
              <div className="tool-output-actions">
                <button className="text-button" onClick={() => void navigator.clipboard?.writeText(item.text)}>Copy output</button>
                {truncated && <button className="text-button" onClick={() => setShowAll(true)}>Show all ({lines.length.toLocaleString()} lines)</button>}
              </div>
              <pre>{shown}</pre>
            </div>}
      </div>}
    </div>
  );
}

/** Two or more tool steps in a row. Open while a step runs, collapsed once they all finish. */
export function ToolGroup({ items, running, children }: { items: ViewItem[]; running: boolean; children: (item: ViewItem) => JSX.Element }) {
  const [manual, setManual] = useState<boolean | null>(null);
  const open = manual ?? running;
  const failed = items.filter(item => toolSummary(item).state === "failed").length;
  return (
    <details className="tool-group" open={open} onToggle={event => setManual((event.target as HTMLDetailsElement).open)}>
      <summary>{items.length} steps{running ? " · running…" : ""}{failed ? ` · ${failed} failed` : ""}</summary>
      {items.map(item => <div key={item.id}>{children(item)}</div>)}
    </details>
  );
}
