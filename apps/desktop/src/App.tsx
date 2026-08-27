import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { desktop } from "./api";
import type { ActivityItem, ChatEvent, ChatMessage, ConnectionState, Screen, TaskSummary } from "./types";

const fallbackConnection: ConnectionState = {
  api_url: "https://ai.syntarus.com/smara-api",
  workspace: "default",
  model_profile: "default",
  paired: false,
  executor_id: null,
  capabilities: [],
  allowed_roots: [],
  terminal_allowlist: [],
  browser_domains: [],
  paused: false,
  running: false,
  pid: null,
  log_path: "",
  has_cli_token: false,
  last_error: null,
};

const starterPrompts = [
  "Summarize what is waiting on my desktop",
  "Research the latest changes in Python and cite sources",
  "Help me plan a safe cleanup of this workspace",
];

function uid(prefix: string) {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function splitLines(value: string) {
  return value.split(/\r?\n|,/).map((item) => item.trim()).filter(Boolean);
}

function formatTime(value?: string) {
  if (!value) return "just now";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "recently" : date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function Icon({ children }: { children: string }) {
  return <span className="icon" aria-hidden="true">{children}</span>;
}

function Button({ children, onClick, kind = "secondary", disabled = false }: { children: ReactNode; onClick?: () => void; kind?: "primary" | "secondary" | "quiet" | "danger"; disabled?: boolean }) {
  return <button className={`button button-${kind}`} onClick={onClick} disabled={disabled}>{children}</button>;
}

function StatusPill({ connection }: { connection: ConnectionState }) {
  const label = !connection.paired ? "Pair desktop" : connection.paused ? "Paused" : connection.running ? "Executor online" : "Executor stopped";
  const tone = !connection.paired ? "amber" : connection.paused ? "amber" : connection.running ? "green" : "muted";
  return <span className={`status-pill status-${tone}`}><span className="status-dot" />{label}</span>;
}

function App() {
  const [screen, setScreen] = useState<Screen>("chat");
  const [connection, setConnection] = useState<ConnectionState>(fallbackConnection);
  const [remote, setRemote] = useState<{ ok: boolean; detail: string } | null>(null);
  const [tasks, setTasks] = useState<TaskSummary[]>([]);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [activity, setActivity] = useState<ActivityItem[]>([]);
  const [draft, setDraft] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [loading, setLoading] = useState(true);
  const [notice, setNotice] = useState<string | null>(null);
  const assistantId = useRef<string | null>(null);
  const conversationId = useRef(`desktop-${Date.now()}`);

  const refreshConnection = useCallback(async () => {
    try {
      const next = await desktop.connection();
      setConnection(next);
      try {
        const health = await desktop.checkConnection(next.api_url);
        setRemote({ ok: health.ok, detail: health.detail });
      } catch (error) {
        setRemote({ ok: false, detail: error instanceof Error ? error.message : "Hosted service is unavailable" });
      }
      return next;
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Desktop settings could not be read");
      setLoading(false);
      return null;
    }
  }, []);

  const refreshTasks = useCallback(async () => {
    try {
      setTasks(await desktop.tasks());
    } catch (error) {
      const message = error instanceof Error ? error.message : "Sign in to see hosted tasks";
      setNotice(message.includes("401") ? "Sign in to Smara Web or CLI to load your hosted tasks." : message);
    }
  }, []);

  useEffect(() => {
    void (async () => {
      await refreshConnection();
      setLoading(false);
      await refreshTasks();
    })();
  }, [refreshConnection, refreshTasks]);

  const handleChatEvent = useCallback((event: ChatEvent) => {
    const target = assistantId.current;
    if (!target) return;
    const type = event.type || "status";
    if (type === "token" && event.text) {
      setMessages((items) => items.map((item) => item.id === target ? { ...item, text: item.text + event.text, pending: true } : item));
    } else if (type === "phase") {
      setActivity((items) => [{ id: uid("phase"), tone: "blue" as const, label: `Agent ${event.phase || "working"}` }, ...items].slice(0, 8));
    } else if (type === "status") {
      setActivity((items) => [{ id: uid("status"), tone: "muted" as const, label: event.label || "Working" }, ...items].slice(0, 8));
    } else if (type === "tool_call") {
      setActivity((items) => [{ id: uid("tool"), tone: "amber" as const, label: `Using ${event.name || "tool"}` }, ...items].slice(0, 8));
    } else if (type === "tool_result") {
      const tone: ActivityItem["tone"] = event.ok ? "green" : "red";
      setActivity((items) => [{ id: uid("result"), tone, label: `${event.name || "Tool"} ${event.ok ? "completed" : "failed"}`, detail: event.preview }, ...items].slice(0, 8));
    } else if (type === "done") {
      setStreaming(false);
      setMessages((items) => items.map((item) => item.id === target ? { ...item, pending: false } : item));
      setActivity((items) => [{ id: uid("done"), tone: "green" as const, label: "Response ready", detail: event.total_ms ? `${event.total_ms} ms` : undefined }, ...items].slice(0, 8));
      assistantId.current = null;
    } else if (type === "error") {
      setStreaming(false);
      setMessages((items) => items.map((item) => item.id === target ? { ...item, pending: false, failed: true, text: event.message || "Smara could not complete this turn." } : item));
      assistantId.current = null;
    }
  }, []);

  useEffect(() => {
    let unlisten: (() => void) | undefined;
    void desktop.onChatEvent((event) => handleChatEvent(event)).then((dispose) => { unlisten = dispose; });
    return () => unlisten?.();
  }, [handleChatEvent]);

  async function send(message = draft) {
    const text = message.trim();
    if (!text || streaming) return;
    setDraft("");
    const answerId = uid("assistant");
    assistantId.current = answerId;
    setMessages((items) => [...items, { id: uid("user"), role: "user", text }, { id: answerId, role: "assistant", text: "", pending: true }]);
    setActivity((items) => [{ id: uid("send"), tone: "blue" as const, label: "Starting hosted Smara" }, ...items].slice(0, 8));
    setStreaming(true);
    try {
      await desktop.streamChat({ api_url: connection.api_url, workspace: connection.workspace, message: text, conversation_id: conversationId.current, model_profile: connection.model_profile });
    } catch (error) {
      setStreaming(false);
      setMessages((items) => items.map((item) => item.id === answerId ? { ...item, pending: false, failed: true, text: error instanceof Error ? error.message : "Smara could not start this turn." } : item));
      assistantId.current = null;
    }
  }

  async function runAction(action: () => Promise<ConnectionState>, success: string) {
    try {
      const next = await action();
      setConnection(next);
      setNotice(success);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "The desktop action failed");
    }
  }

  const navItems = useMemo(() => [
    ["chat", "Chat", "⌁"],
    ["activity", "Activity", "◌"],
    ["settings", "Settings", "⚙"],
  ] as const, []);

  return <div className="app-shell">
    <aside className="sidebar">
      <div className="brand"><div className="brand-mark">✦</div><div><div className="brand-name">Smara</div><div className="brand-sub">desktop companion</div></div></div>
      <div className="workspace-switch"><span className="workspace-dot" /><div><span className="workspace-label">Workspace</span><strong>{connection.workspace}</strong></div><span className="chevron">⌄</span></div>
      <nav className="nav" aria-label="Main navigation">
        {navItems.map(([value, label, icon]) => <button key={value} className={`nav-item ${screen === value ? "nav-active" : ""}`} onClick={() => setScreen(value)}><Icon>{icon}</Icon>{label}{value === "activity" && tasks.some((task) => task.status === "waiting_approval") && <span className="nav-badge">!</span>}</button>)}
      </nav>
      <div className="sidebar-bottom"><div className="privacy-note"><span className="lock">▣</span><div><strong>Private by design</strong><span>Files, browser and terminal stay here.</span></div></div><button className="help-link" onClick={() => void desktop.openWeb()}>Open Smara Web <span>↗</span></button></div>
    </aside>

    <main className="main-panel">
      <header className="topbar"><div className="topbar-title"><span className="eyebrow">LOCAL COMPANION</span><h1>{screen === "chat" ? "Chat" : screen === "activity" ? "Activity" : "Settings"}</h1></div><div className="topbar-actions"><StatusPill connection={connection} /><span className={`remote-pill ${remote?.ok ? "remote-online" : ""}`}><span className="status-dot" />{remote?.ok ? "Hosted connected" : loading ? "Checking…" : "Hosted offline"}</span><button className="icon-button" onClick={() => void refreshConnection()} aria-label="Refresh connection">↻</button></div></header>
      {notice && <div className="notice" role="status"><span>i</span>{notice}<button onClick={() => setNotice(null)} aria-label="Dismiss">×</button></div>}
      {screen === "chat" && <ChatScreen messages={messages} draft={draft} setDraft={setDraft} onSend={() => void send()} onStarter={(value) => void send(value)} streaming={streaming} activity={activity} onOpenWeb={() => void desktop.openWeb()} />}
      {screen === "activity" && <ActivityScreen connection={connection} tasks={tasks} onRefresh={() => void Promise.all([refreshTasks(), refreshConnection()])} onStart={() => void runAction(desktop.start, "Desktop executor started.")} onStop={() => void runAction(desktop.stop, "Desktop executor stopped.")} onPause={() => void runAction(desktop.pause, "Desktop executor paused.")} onResume={() => void runAction(desktop.resume, "Desktop executor resumed.")} onRevoke={() => { if (window.confirm("Revoke this desktop? Approved local work will stop and you will need to pair again.")) void runAction(desktop.revoke, "Desktop executor revoked. Pair again to reconnect."); }} onOpenWeb={() => void desktop.openWeb()} onReadLog={() => desktop.log()} />}
      {screen === "settings" && <SettingsScreen connection={connection} onSaved={(next) => { setConnection(next); setNotice("Desktop settings saved."); }} onPaired={(next) => { setConnection(next); setNotice("Desktop paired. Start the executor when you are ready."); }} />}
    </main>
  </div>;
}

function ChatScreen({ messages, draft, setDraft, onSend, onStarter, streaming, activity, onOpenWeb }: { messages: ChatMessage[]; draft: string; setDraft: (value: string) => void; onSend: () => void; onStarter: (value: string) => void; streaming: boolean; activity: ActivityItem[]; onOpenWeb: () => void }) {
  return <section className="chat-layout"><div className="chat-column"><div className="hero"><div className="hero-orb"><span>✦</span></div><span className="eyebrow">SMARA WORKSPACE</span><h2>What are we working on?</h2><p>Ask the hosted agent anything. Approved local work comes back to this desktop.</p></div>{messages.length === 0 ? <div className="starter-grid">{starterPrompts.map((prompt) => <button className="starter" key={prompt} onClick={() => onStarter(prompt)}><span>{prompt}</span><span className="arrow">↗</span></button>)}</div> : <div className="transcript" aria-live="polite">{messages.map((message) => <div className={`message-row message-${message.role}`} key={message.id}><div className="avatar">{message.role === "user" ? "S" : "✦"}</div><div className={`message ${message.failed ? "message-failed" : ""}`}>{message.text || (message.pending ? <span className="typing"><i /><i /><i /></span> : "")}{message.pending && message.text && <span className="cursor" />}</div></div>)}</div>}<div className="composer-wrap"><div className="composer"><textarea value={draft} onChange={(event) => setDraft(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); onSend(); } }} placeholder="Message Smara…" rows={1} disabled={streaming} /><button className="send-button" onClick={onSend} disabled={!draft.trim() || streaming} aria-label="Send message">↑</button></div><div className="composer-hint"><span>Enter to send</span><span>Shift + Enter for a new line</span><span className="model-hint">Hosted agent · local actions approval-gated</span></div></div></div><aside className="live-rail"><div className="rail-heading"><span>Live activity</span><span className={streaming ? "pulse" : ""}>{streaming ? "working" : "ready"}</span></div>{activity.length === 0 ? <div className="rail-empty"><div className="rail-icon">◌</div><strong>Your work appears here</strong><p>Tool calls, approvals and local execution stay visible without interrupting chat.</p></div> : <div className="activity-list">{activity.map((item) => <div className="activity-item" key={item.id}><span className={`activity-dot activity-${item.tone}`} /><div><strong>{item.label}</strong>{item.detail && <span>{item.detail}</span>}</div></div>)}</div>}<div className="rail-footer"><span className="lock">▣</span><span>Local files and credentials never leave this device.</span></div><Button kind="quiet" onClick={onOpenWeb}>Open full workspace ↗</Button></aside></section>;
}

function ActivityScreen({ connection, tasks, onRefresh, onStart, onStop, onPause, onResume, onRevoke, onOpenWeb, onReadLog }: { connection: ConnectionState; tasks: TaskSummary[]; onRefresh: () => void; onStart: () => void; onStop: () => void; onPause: () => void; onResume: () => void; onRevoke: () => void; onOpenWeb: () => void; onReadLog: () => Promise<string> }) {
  const [showLog, setShowLog] = useState(false);
  const [log, setLog] = useState("No local executor log yet.");
  const [loadingLog, setLoadingLog] = useState(false);
  const waiting = tasks.filter((task) => task.status === "waiting_approval");
  async function toggleLog() {
    if (showLog) { setShowLog(false); return; }
    setLoadingLog(true);
    try { setLog(await onReadLog()); } finally { setLoadingLog(false); setShowLog(true); }
  }
  return <section className="content-page"><div className="page-intro"><div><span className="eyebrow">CONTROL CENTER</span><h2>Local activity</h2><p>See what the hosted agent is asking this PC to do, and stop it at any time.</p></div><Button onClick={onRefresh}>Refresh</Button></div><div className="executor-banner"><div className="executor-main"><div className={`executor-icon ${connection.running ? "executor-online" : ""}`}>⌘</div><div><span className="eyebrow">PAIRED DEVICE</span><h3>{connection.paired ? "This desktop" : "No desktop paired"}</h3><p>{connection.paired ? `${connection.executor_id} · ${connection.capabilities.length} capabilities declared` : "Pair this device in Settings to receive approved local work."}</p></div></div><div className="executor-actions">{connection.running ? <><Button onClick={connection.paused ? onResume : onPause}>{connection.paused ? "Resume" : "Pause"}</Button><Button kind="quiet" onClick={onStop}>Stop</Button></> : <Button kind="primary" onClick={onStart} disabled={!connection.paired}>Start executor</Button>}</div></div>{waiting.length > 0 && <div className="callout callout-amber"><span>!</span><div><strong>{waiting.length} task{waiting.length === 1 ? "" : "s"} waiting for approval</strong><p>Review and approve work in Smara Web before anything can run locally.</p></div><Button kind="quiet" onClick={onOpenWeb}>Review ↗</Button></div>}<div className="section-heading"><h3>Hosted tasks</h3><span>{tasks.length} total</span></div><div className="task-list">{tasks.length === 0 ? <div className="empty-state"><span>◌</span><strong>No hosted tasks loaded</strong><p>Sign in to Smara Web or CLI, then refresh.</p></div> : tasks.slice(0, 12).map((task) => <div className="task-row" key={task.id}><span className={`task-dot task-${task.status}`} /><div className="task-copy"><strong>{task.title}</strong><span>{task.result_summary || task.objective}</span></div><span className={`task-status task-status-${task.status}`}>{task.status.replaceAll("_", " ")}</span><span className="task-time">{formatTime(task.updated_at || task.created_at)}</span></div>)}</div><div className="log-card"><div><strong>Local executor log</strong><span>Only the last bounded lines are shown; secrets are never displayed.</span></div><Button kind="quiet" onClick={() => void toggleLog()}>{loadingLog ? "Loading…" : showLog ? "Hide log" : "View log"}</Button>{showLog && <pre className="log-output">{log}</pre>}</div><div className="danger-zone"><div><strong>Revoke this desktop</strong><span>Immediately invalidates its paired token. You can pair again later.</span></div><Button kind="danger" onClick={onRevoke} disabled={!connection.paired}>Revoke</Button></div></section>;
}

function SettingsScreen({ connection, onSaved, onPaired }: { connection: ConnectionState; onSaved: (next: ConnectionState) => void; onPaired: (next: ConnectionState) => void }) {
  const [apiUrl, setApiUrl] = useState(connection.api_url);
  const [workspace, setWorkspace] = useState(connection.workspace);
  const [modelProfile, setModelProfile] = useState(connection.model_profile);
  const [roots, setRoots] = useState(connection.allowed_roots.join("\n"));
  const [terminal, setTerminal] = useState(connection.terminal_allowlist.join("\n"));
  const [domains, setDomains] = useState(connection.browser_domains.join("\n"));
  const [code, setCode] = useState("");
  const [pairing, setPairing] = useState(false);
  useEffect(() => { setApiUrl(connection.api_url); setWorkspace(connection.workspace); setModelProfile(connection.model_profile); setRoots(connection.allowed_roots.join("\n")); setTerminal(connection.terminal_allowlist.join("\n")); setDomains(connection.browser_domains.join("\n")); }, [connection]);
  async function save() {
    try { onSaved(await desktop.saveSettings({ api_url: apiUrl.trim(), workspace: workspace.trim() || "default", model_profile: modelProfile.trim() || "default", allowed_roots: splitLines(roots), terminal_allowlist: splitLines(terminal), browser_domains: splitLines(domains) })); } catch (error) { alert(error instanceof Error ? error.message : "Could not save settings"); }
  }
  async function pair() {
    if (!code.trim()) return;
    setPairing(true);
    try { onPaired(await desktop.pair({ api_url: apiUrl.trim(), code: code.trim(), allowed_roots: splitLines(roots), terminal_allowlist: splitLines(terminal), browser_domains: splitLines(domains) })); setCode(""); } catch (error) { alert(error instanceof Error ? error.message : "Pairing failed"); } finally { setPairing(false); }
  }
  return <section className="content-page settings-page"><div className="page-intro"><div><span className="eyebrow">DESKTOP CONFIGURATION</span><h2>Settings</h2><p>Keep the local boundary clear. The hosted agent can ask; this PC decides what is allowed.</p></div><Button kind="primary" onClick={() => void save()}>Save changes</Button></div><div className="settings-grid"><div className="settings-card"><div className="card-heading"><span className="card-icon">◉</span><div><h3>Connection</h3><p>Where this desktop receives approved work.</p></div></div><label>Smara API URL<input value={apiUrl} onChange={(event) => setApiUrl(event.target.value)} spellCheck={false} /></label><label>Workspace name<input value={workspace} onChange={(event) => setWorkspace(event.target.value)} /></label><label>Model profile<input value={modelProfile} onChange={(event) => setModelProfile(event.target.value)} placeholder="default" spellCheck={false} /></label><div className="connection-help"><span className={`connection-check ${connection.has_cli_token ? "check-on" : ""}`}>{connection.has_cli_token ? "✓" : "i"}</span><span>{connection.has_cli_token ? "CLI session found. Chat and tasks are available." : "Sign in with `smara login` or Smara Web to enable chat and task history."}</span></div></div><div className="settings-card"><div className="card-heading"><span className="card-icon">⌁</span><div><h3>Pair this desktop</h3><p>Paste the one-time code shown in Smara Web.</p></div></div><div className="pair-status"><span className={`status-dot ${connection.paired ? "dot-green" : "dot-amber"}`} /><strong>{connection.paired ? "Paired and scoped" : "Not paired"}</strong>{connection.executor_id && <span>{connection.executor_id}</span>}</div><div className="pair-row"><input value={code} onChange={(event) => setCode(event.target.value.toUpperCase())} placeholder="8-character code" maxLength={8} spellCheck={false} /><Button kind="primary" onClick={() => void pair()} disabled={pairing || code.trim().length !== 8}>{pairing ? "Pairing…" : "Pair device"}</Button></div><p className="small-help">Open Smara Web → Settings → Desktop → Pair device. Codes expire quickly and can be used only once.</p></div><div className="settings-card full-card"><div className="card-heading"><span className="card-icon">⌂</span><div><h3>Local permissions</h3><p>One entry per line. Empty means the capability stays disabled.</p></div></div><div className="permission-grid"><label>Approved folders<textarea value={roots} onChange={(event) => setRoots(event.target.value)} placeholder={'C:\\Users\\you\\Documents'} /></label><label>Terminal executables<textarea value={terminal} onChange={(event) => setTerminal(event.target.value)} placeholder={'python\ngit'} /></label><label>Browser domains<textarea value={domains} onChange={(event) => setDomains(event.target.value)} placeholder={'github.com\nexample.com'} /></label></div><div className="permission-note"><span>▣</span><span>Smara rejects shell operators, path traversal, symlink escapes, unknown executables, unapproved domains, and unapproved tasks. Changing these lists never grants a task approval.</span></div></div><div className="settings-card full-card about-card"><div><span className="eyebrow">ABOUT THIS APP</span><h3>Thin client, one Smara brain</h3><p>This app does not run a second agent or memory database. It keeps the executor responsive on your PC while the hosted Smara service handles chat, planning, research, and durable task state.</p></div><div className="version">v0.1 beta<br /><span>Windows native</span></div></div></div></section>;
}

export default App;
