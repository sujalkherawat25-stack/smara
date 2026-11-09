import { memo } from "react";
import NativeMarkdown from "./NativeMarkdown";
import { ReadAloud } from "./NativeVoice";
import { ToolCard } from "./ui/ToolCard";
import type { ViewItem } from "./nativeView";

type Props = {
  item: ViewItem;
  /** transcriptRowKey(item): the row re-renders only when this changes (plus busy/copied). */
  rowKey: string;
  busy: boolean;
  copied: boolean;
  onCopy: (item: ViewItem) => void;
  onEdit: (text: string) => void;
  onPreviewFile: (path: string) => void;
};

function TranscriptItemView({ item, busy, copied, onCopy, onEdit, onPreviewFile }: Props) {
  if (item.kind !== "assistant" && item.kind !== "user") return <ToolCard item={item} />;
  return (
    <article className={`native-item ${item.kind}`}>
      <div className="item-heading"><div>
        {item.kind === "assistant" && <ReadAloud text={item.text} disabled={busy} />}
        <button className="text-button" title={item.kind === "assistant" ? "Copy response" : "Copy your message"} aria-label={item.kind === "assistant" ? "Copy response" : "Copy your message"} onClick={() => onCopy(item)}>{copied ? "✓ Copied" : item.kind === "assistant" ? "Copy response" : "Copy"}</button>
        {item.kind === "user" && !busy && <button className="text-button" title="Load this text into the composer; sending creates a new follow-up" onClick={() => onEdit(item.text)}>Edit</button>}
      </div></div>
      {item.kind === "assistant" ? <NativeMarkdown text={item.text} onPreviewFile={onPreviewFile} /> : <pre>{item.text}</pre>}
    </article>
  );
}

/** Rows are memoised on their key, so a streaming delta re-renders only the row it changed. */
export const TranscriptItem = memo(TranscriptItemView, (prev, next) =>
  prev.rowKey === next.rowKey && prev.busy === next.busy && prev.copied === next.copied
  && prev.onCopy === next.onCopy && prev.onEdit === next.onEdit && prev.onPreviewFile === next.onPreviewFile);
