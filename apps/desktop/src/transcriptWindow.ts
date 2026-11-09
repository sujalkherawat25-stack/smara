/** Long-chat windowing: render the newest rows, and reveal earlier ones in steps. */
export const INITIAL_WINDOW = 60;
export const WINDOW_STEP = 60;

export function visibleWindow<T>(items: readonly T[], limit: number): { visible: T[]; hidden: number } {
  const shown = Math.min(Math.max(0, Math.floor(limit)), items.length);
  return { visible: items.slice(items.length - shown), hidden: items.length - shown };
}

export function widenWindow(limit: number): number {
  return limit + WINDOW_STEP;
}

export type RowSource = { id: string; kind: string; status?: string; text: string; command?: string; exitCode?: number; durationMs?: number };

/**
 * What decides whether a transcript row must re-render. Streaming appends text, so the length
 * changes with every batched delta; a row whose key is unchanged is skipped by React.memo.
 */
export function transcriptRowKey(item: RowSource): string {
  return [item.id, item.kind, item.status ?? "", item.text.length, item.command ?? "", item.exitCode ?? "", item.durationMs ?? ""].join("\u0000");
}
