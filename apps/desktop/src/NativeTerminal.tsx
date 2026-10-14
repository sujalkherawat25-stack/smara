import { useEffect, useRef, useState } from "react";
import type { TerminalSession } from "./terminalProtocol";

export default function NativeTerminal({ workspace, connected, session, interactiveSupported, onConnect, onStart, onInput, onInterrupt, onStop, onResize, onClear, onClose }: {
  workspace: string;
  connected: boolean;
  session: TerminalSession | null;
  interactiveSupported: boolean;
  onConnect: () => void;
  onStart: () => void;
  onInput: (value: string) => void;
  onInterrupt: () => void;
  onStop: () => void;
  onResize: (cols: number, rows: number) => void;
  onClear: () => void;
  onClose: () => void;
}) {
  const [input, setInput] = useState("");
  const outputRef = useRef<HTMLPreElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const active = session && ["starting", "running", "stopping"].includes(session.status);

  useEffect(() => {
    const output = outputRef.current;
    if (output) output.scrollTop = output.scrollHeight;
  }, [session?.output]);

  useEffect(() => {
    if (!session || !["starting", "running"].includes(session.status)) return;
    const output = outputRef.current;
    const container = output?.parentElement;
    if (!container || typeof ResizeObserver === "undefined") return;
    let last = "";
    let timer: ReturnType<typeof setTimeout> | undefined;
    const observer = new ResizeObserver(entries => {
      const rect = entries[0]?.contentRect;
      if (!rect) return;
      const cols = Math.max(40, Math.min(180, Math.floor(rect.width / 8.1)));
      const rows = Math.max(10, Math.min(60, Math.floor(rect.height / 18)));
      const size = `${cols}:${rows}`;
      if (size === last) return;
      last = size;
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => onResize(cols, rows), 120);
    });
    observer.observe(container);
    return () => { observer.disconnect(); if (timer) clearTimeout(timer); };
  }, [session?.id, session?.status]);

  useEffect(() => { if (session && ["starting", "running"].includes(session.status)) inputRef.current?.focus(); }, [session?.id, session?.status]);

  const submit = () => {
    if (!input.trim() || !session || session.status !== "running") return;
    onInput(`${input}\r`);
    setInput("");
  };

  return <section className="agent-terminal-panel interactive-terminal" aria-label="Interactive terminal">
    <div className="panel-heading"><div><strong>PowerShell</strong><small title={workspace}>{workspace}</small></div><button aria-label="Close terminal panel" onClick={onClose}>×</button></div>
    {!interactiveSupported ? <div className="terminal-unavailable" role="status"><strong>Interactive terminal needs Windows MXC support</strong><p>No interactive shell will start without the verified MXC backend. There is no weaker fallback. Agent command results remain available in the conversation.</p></div> : <>
    <p className="terminal-sandbox-note">MXC sandbox · writes to this project · network disabled</p>
    <div className="terminal-toolbar">
      {!connected && <button onClick={onConnect}>Connect project</button>}
      {!active && <button disabled={!connected} onClick={onStart}>{session ? "Restart shell" : "Start shell"}</button>}
      {active && <><button disabled={session?.status !== "running"} onClick={onInterrupt} title="Send Ctrl+C to the shell">Interrupt</button><button className="terminal-stop" onClick={onStop}>Stop shell</button></>}
      <button disabled={!session?.output} onClick={onClear}>Clear output</button>
      {session && <small role="status">{session.status}{session.exitCode !== undefined ? ` · exit ${session.exitCode}` : ""}</small>}
    </div>
    {session?.error && <p className="terminal-error" role="alert">{session.error}</p>}
    {!session && <p className="terminal-empty">Start a sandboxed shell in the selected project. It does not inherit a personal shell profile.</p>}
    <pre ref={outputRef} className="interactive-terminal-output" aria-label="Terminal output" aria-live="off">{session?.output || (session?.status === "starting" ? "Starting the sandboxed shell…" : "")}{session?.truncated && "\n[Earlier output omitted to keep the terminal responsive.]"}</pre>
    {session?.status === "running" && <form className="interactive-terminal-input" onSubmit={event => { event.preventDefault(); submit(); }}>
      <textarea ref={inputRef} aria-label="Terminal input" rows={2} value={input} placeholder="Type a command and press Enter" onChange={event => setInput(event.target.value)} onKeyDown={event => {
        if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); submit(); }
      }} />
      <button type="submit" disabled={!input.trim()}>Send</button>
    </form>}
    </>}
  </section>;
}
