import type { ReactNode } from "react";
import { invoke } from "@tauri-apps/api/core";
/** Small safe presentation parser: React escapes all text; no HTML injection. */
function inline(text: string): ReactNode[] {
  return text.split(/(`[^`\n]+`|\*\*[^*\n]+\*\*|\[[^\]\n]+\]\(https?:\/\/[^\s)]+\))/g).map((part, index) => {
    if (part.startsWith("`")) return <code key={index}>{part.slice(1, -1)}</code>;
    if (part.startsWith("**")) return <strong key={index}>{part.slice(2, -2)}</strong>;
    const link = /^\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)$/.exec(part);
    if (link) return <a key={index} href={link[2]} onClick={event => { event.preventDefault(); void invoke("native_open_url", { url: link[2] }).catch(() => {}); }}>{link[1]}</a>;
    return part;
  });
}
export default function NativeMarkdown({ text }: { text: string }) {
  const blocks: ReactNode[] = [];
  const lines = text.split("\n");
  let code: string[] | null = null;
  let list: string[] = [];
  let ordered = false;
  const flush = () => {
    if (!list.length) return;
    const children = list.map((line, i) => <li key={i}>{inline(line)}</li>);
    blocks.push(ordered ? <ol key={blocks.length}>{children}</ol> : <ul key={blocks.length}>{children}</ul>);
    list = [];
  };
  for (const line of lines) {
    if (line.startsWith("```")) { flush(); if (code === null) code = []; else { blocks.push(<pre className="code-block" key={blocks.length}><code>{code.join("\n")}</code></pre>); code = null; } continue; }
    if (code !== null) { code.push(line); continue; }
    const bullet = /^\s*(?:[-*]\s+|\d+\.\s+)(.*)$/.exec(line);
    if (bullet) { const nextOrdered = /^\s*\d+\./.test(line); if (list.length && nextOrdered !== ordered) flush(); ordered = nextOrdered; list.push(bullet[1]); continue; }
    flush();
    const heading = /^(#{1,4})\s+(.+)$/.exec(line);
    if (heading) blocks.push(<h3 key={blocks.length}>{inline(heading[2])}</h3>);
    else if (line.trim()) blocks.push(<p key={blocks.length}>{inline(line)}</p>);
  }
  flush(); if (code !== null) blocks.push(<pre className="code-block" key={blocks.length}><code>{code.join("\n")}</code></pre>);
  return <div className="native-markdown">{blocks}</div>;
}
