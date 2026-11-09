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

export type RowProps<T> = { item: T; busy: boolean; copied: boolean; onCopy: unknown; onEdit: unknown; onPreviewFile: unknown };

/**
 * Whether a memoised transcript row can be reused. Rows are compared by object identity: mergeItem and the
 * delta updates build a new object for every row they change and keep the old object for the rest. A key
 * built from text length would miss same-length replacements such as "3/10" to "4/10", so none is used.
 */
export function sameTranscriptRow<T>(prev: RowProps<T>, next: RowProps<T>): boolean {
  return prev.item === next.item && prev.busy === next.busy && prev.copied === next.copied
    && prev.onCopy === next.onCopy && prev.onEdit === next.onEdit && prev.onPreviewFile === next.onPreviewFile;
}
