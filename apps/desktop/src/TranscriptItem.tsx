import { memo } from "react";
import NativeMarkdown from "./NativeMarkdown";
import NativeCommand from "./NativeCommand";
import { ReadAloud } from "./NativeVoice";
import type { ViewItem } from "./nativeView";
import { sameTranscriptRow } from "./transcriptWindow";

type Props = {
  item: ViewItem;
  busy: boolean;
  copied: boolean;
  onCopy: (item: ViewItem) => void;
  onEdit: (text: string) => void;
  onPreviewFile: (path: string) => void;
};

function TranscriptItemView({ item, busy, copied, onCopy, onEdit, onPreviewFile }: Props) {
  const isMessage = item.kind === "assistant" || item.kind === "user";
  return (
    <article className={`native-item ${item.kind}`}>
      <div className="item-heading">{!isMessage && <small>{item.kind} {item.status && `· ${item.status}`}</small>}<div>
        {item.kind === "assistant" && <ReadAloud text={item.text} disabled={busy} />}
        {isMessage && <button className="text-button" title={item.kind === "assistant" ? "Copy response" : "Copy your message"} aria-label={item.kind === "assistant" ? "Copy response" : "Copy your message"} onClick={() => onCopy(item)}>{copied ? "✓ Copied" : item.kind === "assistant" ? "Copy response" : "Copy"}</button>}
        {item.kind === "user" && !busy && <button className="text-button" title="Load this text into the composer; sending creates a new follow-up" onClick={() => onEdit(item.text)}>Edit</button>}
      </div></div>
      {item.kind === "assistant" ? <NativeMarkdown text={item.text} onPreviewFile={onPreviewFile} /> : item.kind === "user" ? <pre>{item.text}</pre> : item.kind === "commandExecution" ? <NativeCommand item={item} /> : <details open={item.status === "inProgress" || item.status === "failed"}><summary>Tool details · {item.text.length.toLocaleString()} characters</summary><pre>{item.text}</pre></details>}
    </article>
  );
}

/** A streaming delta re-renders only the row whose object changed; the others are reused (see sameTranscriptRow). */
export const TranscriptItem = memo(TranscriptItemView, sameTranscriptRow);
