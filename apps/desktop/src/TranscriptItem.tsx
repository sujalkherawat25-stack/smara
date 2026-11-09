import { memo } from "react";
import NativeMarkdown from "./NativeMarkdown";
import { ReadAloud } from "./NativeVoice";
import { ToolCard } from "./ui/ToolCard";
import type { ViewItem } from "./nativeView";
import { sameTranscriptRow } from "./transcriptWindow";

type Props = {
  item: ViewItem;
  busy: boolean;
  copied: boolean;
  onCopy: (item: ViewItem) => void;
  onEdit: (text: string) => void;
  onPreviewFile: (path: string) => void;
  /** The agent's avatar letter and colour, shown beside its replies. */
  agentInitial?: string;
  agentTone?: string;
};

/**
 * Your messages sit on the right in a bubble sized to the text. Replies sit on the left beside the agent's avatar,
 * in a quiet bubble. Tool steps are not messages; they render as their own rows.
 */
function TranscriptItemView({ item, busy, copied, onCopy, onEdit, onPreviewFile, agentInitial = "S", agentTone = "teal" }: Props) {
  if (item.kind === "separator") return <div className="history-separator" role="separator"><span>{item.text}</span></div>;
  if (item.kind !== "assistant" && item.kind !== "user") return <ToolCard item={item} />;
  const reply = item.kind === "assistant";
  return (
    <article className={`native-item ${item.kind}`}>
      {reply && <span className="avatar message-avatar" data-tone={agentTone} aria-hidden="true">{agentInitial}</span>}
      <div className="message-main">
        <div className="item-heading"><div>
          {reply && <ReadAloud text={item.text} disabled={busy} />}
          <button className="text-button" title={reply ? "Copy response" : "Copy your message"} aria-label={reply ? "Copy response" : "Copy your message"} onClick={() => onCopy(item)}>{copied ? "Copied" : reply ? "Copy response" : "Copy"}</button>
          {!reply && !busy && <button className="text-button" title="Load this text into the composer; sending creates a new follow-up" onClick={() => onEdit(item.text)}>Edit</button>}
        </div></div>
        {reply ? <div className="message-bubble"><NativeMarkdown text={item.text} onPreviewFile={onPreviewFile} /></div> : <pre>{item.text}</pre>}
      </div>
    </article>
  );
}

/** A streaming delta re-renders only the row whose object changed; the others are reused (see sameTranscriptRow). */
export const TranscriptItem = memo(TranscriptItemView, sameTranscriptRow);
