import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode, type RefObject } from "react";
import { desktop, isNativeDesktop } from "./api";
import type { ActivityItem, ChatEvent, ChatMessage, ConnectionState, LocalCredentialSummary, LocalModelProfile, Screen, TaskSummary } from "./types";
import smaraLogo from "./assets/smara-logo.svg";

const fallbackConnection: ConnectionState = {
  api_url: "https://ai.syntarus.com/smara-api",
  web_url: "https://ai.syntarus.com/",
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

const modelProfiles = [
  { value: "default", label: "Automatic", provider: "Smara hosted", description: "Let the hosted service choose the best configured model for this workspace.", tone: "blue" },
  { value: "grok", label: "Grok", provider: "xAI · hosted", description: "Fast general-purpose reasoning and tool planning.", tone: "green" },
  { value: "sarvam", label: "Sarvam", provider: "Sarvam AI · hosted", description: "Indic + English conversations when enabled by the operator.", tone: "amber" },
  { value: "sarvam-reasoning", label: "Sarvam Reasoning", provider: "Sarvam AI · hosted", description: "GLM-5.2 for deeper, long-context reasoning (beta access required).", tone: "purple" },
  { value: "sarvam-vision", label: "Sarvam Vision", provider: "Sarvam AI · hosted", description: "Gemma 4 for image understanding (beta access required).", tone: "blue" },
];

const credentialPresets = [
  { value: "tavily", label: "Tavily Search", name: "TAVILY_API_KEY", description: "Web research and source discovery from this PC." },
  { value: "github", label: "GitHub", name: "GITHUB_TOKEN", description: "Approved local GitHub or repository actions." },
  { value: "custom", label: "Custom local tool", name: "", description: "Any other secret used by an approved local command." },
];

function uid(prefix: string) {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function splitLines(value: string) {
  return value.split(/\r?\n|,/).map((item) => item.trim()).filter(Boolean);
}

/**
 * Pairing codes are deliberately short-lived hex strings. Users commonly
 * paste them with a line break or a space copied from the web UI, so keep the
 * field forgiving while still sending only the canonical eight characters to
 * the native bridge.
 */
function normalizePairingCode(value: string) {
  return value.replace(/[^0-9a-f]/gi, "").toUpperCase().slice(0, 8);
}

function errorMessage(error: unknown, fallback: string) {
  if (error instanceof Error && error.message) return error.message;
  if (typeof error === "string" && error.trim()) return error;
  if (error && typeof error === "object" && "message" in error && typeof error.message === "string") return error.message;
  return fallback;
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
  const [signingIn, setSigningIn] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const assistantId = useRef<string | null>(null);
  const conversationId = useRef(`desktop-${Date.now()}`);
  const transcriptEndRef = useRef<HTMLDivElement>(null!);

  const refreshConnection = useCallback(async () => {
    if (!isNativeDesktop) {
      setConnection(fallbackConnection);
      setRemote({ ok: true, detail: "Desktop UI preview" });
      setLoading(false);
      return fallbackConnection;
    }
    try {
      const next = await desktop.connection();
      setConnection(next);
      try {
        const health = await desktop.checkConnection(next.api_url);
        setRemote({ ok: health.ok, detail: health.detail });
      } catch (error) {
        setRemote({ ok: false, detail: errorMessage(error, "Hosted service is unavailable") });
      }
      return next;
    } catch (error) {
      setRemote({ ok: false, detail: errorMessage(error, "Hosted service is unavailable") });
      setNotice(errorMessage(error, "Desktop settings could not be read"));
      setLoading(false);
      return null;
    }
  }, []);

  const refreshTasks = useCallback(async () => {
    if (!isNativeDesktop) return;
    try {
      setTasks(await desktop.tasks());
    } catch (error) {
      const message = errorMessage(error, "Sign in to see hosted tasks");
      if (message.includes("401")) {
        setConnection((current) => ({ ...current, has_cli_token: false }));
        setNotice("Your Smara sign-in expired. Sign in again to load chat and hosted tasks.");
      } else {
        setNotice(message);
      }
    }
  }, []);

  useEffect(() => {
    void (async () => {
      const next = await refreshConnection();
      setLoading(false);
      if (next?.has_cli_token) await refreshTasks();
    })();
  }, [refreshConnection, refreshTasks]);

  useEffect(() => {
    if (screen !== "activity" || !connection.has_cli_token || !isNativeDesktop) return;
    const timer = window.setInterval(() => {
      void Promise.all([refreshTasks(), refreshConnection()]);
    }, 5000);
    return () => window.clearInterval(timer);
  }, [screen, connection.has_cli_token, refreshConnection, refreshTasks]);

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
      const detail = event.message || "Smara could not complete this turn.";
      setMessages((items) => items.map((item) => item.id === target ? {
        ...item,
        pending: false,
        failed: true,
        // Preserve a useful partial answer if the provider disconnected after
        // streaming began; show the actionable cause underneath it.
        text: item.text || detail,
        error: item.text ? detail : undefined,
      } : item));
      setActivity((items) => [{ id: uid("error"), tone: "red" as const, label: "Hosted response failed", detail }, ...items].slice(0, 8));
      assistantId.current = null;
    }
  }, []);

  useEffect(() => {
    if (!isNativeDesktop) return;
    let unlisten: (() => void) | undefined;
    void desktop.onChatEvent((event) => handleChatEvent(event)).then((dispose) => { unlisten = dispose; }).catch((error) => setNotice(errorMessage(error, "Live chat events could not be connected.")));
    return () => unlisten?.();
  }, [handleChatEvent]);

  async function send(message = draft) {
    const text = message.trim();
    if (!text || streaming) return;
    if (!isNativeDesktop) {
      setNotice("This is the browser preview. Open the installed Smara Desktop app to chat and run local work.");
      return;
    }
    const localModelSelected = connection.model_profile.startsWith("local:");
    if (!connection.has_cli_token && !localModelSelected) {
      setScreen("settings");
      setNotice("Sign in to Smara, or add a private model in Settings before starting a chat.");
      return;
    }
    setDraft("");
    const answerId = uid("assistant");
    assistantId.current = answerId;
    setMessages((items) => [...items, { id: uid("user"), role: "user", text }, { id: answerId, role: "assistant", text: "", pending: true }]);
    setActivity((items) => [{ id: uid("send"), tone: "blue" as const, label: localModelSelected ? "Starting private desktop model" : "Starting hosted Smara" }, ...items].slice(0, 8));
    setStreaming(true);
    try {
      await desktop.streamChat({ api_url: connection.api_url, workspace: connection.workspace, message: text, conversation_id: conversationId.current, model_profile: connection.model_profile });
    } catch (error) {
      setStreaming(false);
      const message = errorMessage(error, "Smara could not start this turn.");
      if (message.includes("401")) {
        setConnection((current) => ({ ...current, has_cli_token: false }));
        setScreen("settings");
        setNotice("Your Smara sign-in expired. Sign in again, then retry the message.");
      }
      setMessages((items) => items.map((item) => item.id === answerId ? {
        ...item,
        pending: false,
        failed: true,
        text: item.text || message,
        error: item.text ? message : undefined,
      } : item));
      setActivity((items) => [{ id: uid("error"), tone: "red" as const, label: "Chat request failed", detail: message }, ...items].slice(0, 8));
      assistantId.current = null;
    }
  }

  async function runAction(action: () => Promise<ConnectionState>, success: string) {
    try {
      const next = await action();
      setConnection(next);
      setNotice(success);
    } catch (error) {
      setNotice(errorMessage(error, "The desktop action failed"));
    }
  }

  async function signIn(apiUrl = connection.api_url, webUrl = connection.web_url) {
    if (signingIn) return;
    setSigningIn(true);
    try {
      setNotice("A browser window will open. Approve Smara Desktop there, then return here.");
      await desktop.login(apiUrl, webUrl);
      await refreshConnection();
      await refreshTasks();
      setNotice("Smara Desktop is signed in.");
    } catch (error) {
      setNotice(errorMessage(error, "Smara sign-in could not be completed."));
    } finally {
      setSigningIn(false);
    }
  }

  const navItems = useMemo(() => [
    ["chat", "Chat", "⌁"],
    ["activity", "Activity", "◌"],
    ["settings", "Settings", "⚙"],
  ] as const, []);

  return <div className="app-shell">
    <aside className="sidebar">
      <div className="brand"><div className="brand-mark"><img src={smaraLogo} alt="" /></div><div><div className="brand-name">Smara</div><div className="brand-sub">desktop companion</div></div></div>
      <div className="workspace-switch"><span className="workspace-dot" /><div><span className="workspace-label">Workspace</span><strong>{connection.workspace}</strong></div><span className="chevron">⌄</span></div>
      <nav className="nav" aria-label="Main navigation">
        {navItems.map(([value, label, icon]) => <button key={value} className={`nav-item ${screen === value ? "nav-active" : ""}`} onClick={() => setScreen(value)}><Icon>{icon}</Icon>{label}{value === "activity" && tasks.some((task) => task.status === "waiting_approval") && <span className="nav-badge">!</span>}</button>)}
      </nav>
      <div className="sidebar-bottom"><div className="privacy-note"><span className="lock">▣</span><div><strong>Private by design</strong><span>Files, browser and terminal stay here.</span></div></div><button className="help-link" onClick={() => void desktop.openWeb()}>Open Smara Web <span>↗</span></button></div>
    </aside>

    <main className="main-panel">
      <header className="topbar"><div className="topbar-title"><span className="eyebrow">LOCAL COMPANION</span><h1>{screen === "chat" ? "Chat" : screen === "activity" ? "Activity" : "Settings"}</h1></div><div className="topbar-actions"><StatusPill connection={connection} />{isNativeDesktop && !connection.has_cli_token && <Button kind="quiet" onClick={() => void signIn()} disabled={signingIn}>{signingIn ? "Signing in…" : "Sign in"}</Button>}<span className={`remote-pill ${remote?.ok ? "remote-online" : ""}`}><span className="status-dot" />{!isNativeDesktop ? "UI preview" : remote?.ok ? connection.has_cli_token ? "Hosted connected" : "Hosted ready · sign in" : loading ? "Checking…" : "Hosted offline"}</span><button className="icon-button" onClick={() => void refreshConnection()} aria-label="Refresh connection">↻</button></div></header>
      {notice && <div className="notice" role="status"><span>i</span>{notice}<button onClick={() => setNotice(null)} aria-label="Dismiss">×</button></div>}
      {screen === "chat" && <ChatScreen messages={messages} draft={draft} setDraft={setDraft} onSend={() => void send()} onStarter={(value) => void send(value)} streaming={streaming} activity={activity} canChat={connection.has_cli_token || connection.model_profile.startsWith("local:")} localModel={connection.model_profile.startsWith("local:")} onSignIn={() => setScreen("settings")} onOpenWeb={() => void desktop.openWeb()} onClearActivity={() => setActivity([])} transcriptEndRef={transcriptEndRef} />}
      {screen === "activity" && <ActivityScreen connection={connection} tasks={tasks} onRefresh={() => void Promise.all([refreshTasks(), refreshConnection()])} onStart={() => void runAction(desktop.start, "Desktop executor started.")} onStop={() => void runAction(desktop.stop, "Desktop executor stopped.")} onPause={() => void runAction(desktop.pause, "Desktop executor paused.")} onResume={() => void runAction(desktop.resume, "Desktop executor resumed.")} onRevoke={() => { if (window.confirm("Revoke this desktop? Approved local work will stop and you will need to pair again.")) void runAction(desktop.revoke, "Desktop executor revoked. Pair again to reconnect."); }} onOpenWeb={() => void desktop.openWeb()} onReadLog={() => desktop.log()} />}
      {screen === "settings" && <SettingsScreen connection={connection} onSaved={(next) => { setConnection(next); setNotice("Desktop settings saved."); }} onPaired={(next) => { setConnection(next); setNotice("Desktop paired. Start the executor when you are ready."); }} onSignIn={(apiUrl, webUrl) => void signIn(apiUrl, webUrl)} />}
    </main>
  </div>;
}

function ChatScreen({ messages, draft, setDraft, onSend, onStarter, streaming, activity, canChat, localModel, onSignIn, onOpenWeb, onClearActivity, transcriptEndRef }: { messages: ChatMessage[]; draft: string; setDraft: (value: string) => void; onSend: () => void; onStarter: (value: string) => void; streaming: boolean; activity: ActivityItem[]; canChat: boolean; localModel: boolean; onSignIn: () => void; onOpenWeb: () => void; onClearActivity: () => void; transcriptEndRef: RefObject<HTMLDivElement> }) {
  useEffect(() => {
    const frame = window.requestAnimationFrame(() => transcriptEndRef.current?.scrollIntoView({ behavior: streaming ? "auto" : "smooth", block: "end" }));
    return () => window.cancelAnimationFrame(frame);
  }, [messages, streaming, transcriptEndRef]);

  return <section className="chat-layout"><div className={`chat-column ${messages.length > 0 ? "has-messages" : ""}`}><div className="hero"><div className="hero-orb"><img src={smaraLogo} alt="Smara" /></div><span className="eyebrow">SMARA WORKSPACE</span><h2>What are we working on?</h2><p>{localModel ? "Private chat stays on this PC. Switch to a hosted profile for task planning and live task history." : "Ask the hosted agent anything. Approved local work comes back to this desktop."}</p>{isNativeDesktop && !canChat && <div className="chat-auth-card"><strong>Sign in or add a model to start</strong><span>Connect this desktop to hosted Smara, or add a private provider in Settings. Your local permissions stay on this PC.</span><Button kind="primary" onClick={onSignIn}>Open Settings ↗</Button></div>}</div>{messages.length === 0 ? <div className="starter-grid">{starterPrompts.map((prompt) => <button className="starter" key={prompt} onClick={() => onStarter(prompt)} disabled={!canChat}><span>{prompt}</span><span className="arrow">↗</span></button>)}</div> : <div className="transcript" aria-live="polite">{messages.map((message) => <div className={`message-row message-${message.role}`} key={message.id}><div className="avatar">{message.role === "user" ? "S" : <img src={smaraLogo} alt="" />}</div><div className={`message ${message.failed ? "message-failed" : ""}`}>{message.text || (message.pending ? <span className="typing"><i /><i /><i /></span> : "")}{message.pending && message.text && <span className="cursor" />}{message.error && message.text && <span className="message-error">{message.error}</span>}</div></div>)}<div ref={transcriptEndRef} aria-hidden="true" /></div>}<div className="composer-wrap"><div className="composer"><textarea value={draft} onChange={(event) => setDraft(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); onSend(); } }} placeholder={canChat ? localModel ? "Message your private model…" : "Message Smara…" : "Sign in or add a model to start…"} rows={1} disabled={streaming || !canChat} /><button className="send-button" onClick={onSend} disabled={!draft.trim() || streaming || !canChat} aria-label="Send message">↑</button></div><div className="composer-hint"><span>Enter to send</span><span>Shift + Enter for a new line</span><span className="model-hint">{localModel ? "Private model · direct from this PC" : "Hosted agent · local actions approval-gated"}</span></div></div></div><aside className="live-rail"><div className="rail-heading"><span>Live activity</span><div className="rail-heading-actions"><span className={streaming ? "pulse" : ""}>{streaming ? "working" : "ready"}</span>{activity.length > 0 && <button type="button" className="rail-clear" onClick={onClearActivity}>Clear</button>}</div></div>{activity.length === 0 ? <div className="rail-empty"><div className="rail-icon">◌</div><strong>Your work appears here</strong><p>Tool calls, approvals and local execution stay visible without interrupting chat.</p></div> : <div className="activity-list">{activity.map((item) => <div className="activity-item" key={item.id}><span className={`activity-dot activity-${item.tone}`} /><div><strong>{item.label}</strong>{item.detail && <span>{item.detail}</span>}</div></div>)}</div>}<div className="rail-footer"><span className="lock">▣</span><span>Local files and credentials never leave this device.</span></div><Button kind="quiet" onClick={onOpenWeb}>Open full workspace ↗</Button></aside></section>;
}

function ActivityScreen({ connection, tasks, onRefresh, onStart, onStop, onPause, onResume, onRevoke, onOpenWeb, onReadLog }: { connection: ConnectionState; tasks: TaskSummary[]; onRefresh: () => void; onStart: () => void; onStop: () => void; onPause: () => void; onResume: () => void; onRevoke: () => void; onOpenWeb: () => void; onReadLog: () => Promise<string> }) {
  const [showLog, setShowLog] = useState(false);
  const [log, setLog] = useState("No local executor log yet.");
  const [loadingLog, setLoadingLog] = useState(false);
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const waiting = tasks.filter((task) => task.status === "waiting_approval");
  const selectedTask = tasks.find((task) => task.id === selectedTaskId) || null;
  async function toggleLog() {
    if (showLog) { setShowLog(false); return; }
    setLoadingLog(true);
    try { setLog(await onReadLog()); }
    catch (error) { setLog(errorMessage(error, "The local executor log could not be read.")); }
    finally { setLoadingLog(false); setShowLog(true); }
  }
  return <section className="content-page"><div className="page-intro"><div><span className="eyebrow">CONTROL CENTER</span><h2>Local activity</h2><p>See what the hosted agent is asking this PC to do, and stop it at any time.</p></div><Button onClick={onRefresh}>Refresh</Button></div><div className="executor-banner"><div className="executor-main"><div className={`executor-icon ${connection.running ? "executor-online" : ""}`}>⌘</div><div><span className="eyebrow">PAIRED DEVICE</span><h3>{connection.paired ? "This desktop" : "No desktop paired"}</h3><p>{connection.paired ? `${connection.executor_id} · ${connection.capabilities.length} capabilities declared` : "Pair this device in Settings to receive approved local work."}</p></div></div><div className="executor-actions">{connection.running ? <><Button onClick={connection.paused ? onResume : onPause}>{connection.paused ? "Resume" : "Pause"}</Button><Button kind="quiet" onClick={onStop}>Stop</Button></> : <Button kind="primary" onClick={onStart} disabled={!connection.paired}>Start executor</Button>}</div></div>{waiting.length > 0 && <div className="callout callout-amber"><span>!</span><div><strong>{waiting.length} task{waiting.length === 1 ? "" : "s"} waiting for approval</strong><p>Review and approve work in Smara Web before anything can run locally.</p></div><Button kind="quiet" onClick={onOpenWeb}>Review ↗</Button></div>}<div className="section-heading"><h3>Hosted tasks</h3><span>{tasks.length} total · refreshes automatically</span></div><div className="task-list">{tasks.length === 0 ? <div className="empty-state"><span>◌</span><strong>No hosted tasks loaded</strong><p>Sign in to Smara, then return here. Activity refreshes automatically.</p></div> : tasks.slice(0, 12).map((task) => <button className={`task-row ${selectedTaskId === task.id ? "task-row-selected" : ""}`} key={task.id} onClick={() => setSelectedTaskId(task.id === selectedTaskId ? null : task.id)}><span className={`task-dot task-${task.status}`} /><span className="task-copy"><strong>{task.title}</strong><span>{task.result || task.objective}</span></span><span className={`task-status task-status-${task.status}`}>{task.status.replaceAll("_", " ")}</span><span className="task-time">{formatTime(task.updated_at || task.created_at)}</span></button>)}</div>{selectedTask && <div className="task-detail"><div><span className="eyebrow">TASK RESULT</span><h3>{selectedTask.title}</h3><p>{selectedTask.objective}</p></div><span className={`task-status task-status-${selectedTask.status}`}>{selectedTask.status.replaceAll("_", " ")}</span><div className="task-result">{selectedTask.result || (selectedTask.status === "completed" ? "The task completed without a written result." : "A final result will appear here when the task completes.")}</div><Button kind="quiet" onClick={onOpenWeb}>Open full task history ↗</Button></div>}<div className="log-card"><div><strong>Local executor log</strong><span>Only the last bounded lines are shown; secrets are never displayed.</span></div><Button kind="quiet" onClick={() => void toggleLog()}>{loadingLog ? "Loading…" : showLog ? "Hide log" : "View log"}</Button>{showLog && <pre className="log-output">{log}</pre>}</div><div className="danger-zone"><div><strong>Revoke this desktop</strong><span>Immediately invalidates its paired token. You can pair again later.</span></div><Button kind="danger" onClick={onRevoke} disabled={!connection.paired}>Revoke</Button></div></section>;
}

function SettingsScreen({ connection, onSaved, onPaired, onSignIn }: { connection: ConnectionState; onSaved: (next: ConnectionState) => void; onPaired: (next: ConnectionState) => void; onSignIn: (apiUrl: string, webUrl: string) => void }) {
  const [apiUrl, setApiUrl] = useState(connection.api_url);
  const [webUrl, setWebUrl] = useState(connection.web_url);
  const [workspace, setWorkspace] = useState(connection.workspace);
  const [modelProfile, setModelProfile] = useState(connection.model_profile);
  const [roots, setRoots] = useState(connection.allowed_roots.join("\n"));
  const [terminal, setTerminal] = useState(connection.terminal_allowlist.join("\n"));
  const [domains, setDomains] = useState(connection.browser_domains.join("\n"));
  const [code, setCode] = useState("");
  const [pairing, setPairing] = useState(false);
  const [credentials, setCredentials] = useState<LocalCredentialSummary[]>([]);
  const [localModelProfiles, setLocalModelProfiles] = useState<LocalModelProfile[]>([]);
  const [credentialProvider, setCredentialProvider] = useState("tavily");
  const [credentialName, setCredentialName] = useState("TAVILY_API_KEY");
  const [credentialSecret, setCredentialSecret] = useState("");
  const [credentialBusy, setCredentialBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  useEffect(() => { setApiUrl(connection.api_url); setWebUrl(connection.web_url); setWorkspace(connection.workspace); setModelProfile(connection.model_profile); setRoots(connection.allowed_roots.join("\n")); setTerminal(connection.terminal_allowlist.join("\n")); setDomains(connection.browser_domains.join("\n")); }, [connection]);
  useEffect(() => {
    if (!isNativeDesktop) return;
    void desktop.credentials().then(setCredentials).catch(() => setCredentials([]));
    void desktop.modelProfiles().then(setLocalModelProfiles).catch(() => setLocalModelProfiles([]));
  }, []);
  async function save(selectedModel = modelProfile) {
    if (!isNativeDesktop) return null;
    setFormError(null);
    try { const next = await desktop.saveSettings({ api_url: apiUrl.trim(), web_url: webUrl.trim(), workspace: workspace.trim() || "default", model_profile: selectedModel.trim() || "default", allowed_roots: splitLines(roots), terminal_allowlist: splitLines(terminal), browser_domains: splitLines(domains) }); onSaved(next); return next; } catch (error) { setFormError(errorMessage(error, "Could not save settings")); return null; }
  }
  async function pair() {
    const normalizedCode = normalizePairingCode(code);
    if (normalizedCode.length !== 8) {
      setFormError(`Enter the complete 8-character pairing code (${normalizedCode.length}/8).`);
      return;
    }
    setPairing(true);
    setFormError(null);
    try { onPaired(await desktop.pair({ api_url: apiUrl.trim(), code: normalizedCode, allowed_roots: splitLines(roots), terminal_allowlist: splitLines(terminal), browser_domains: splitLines(domains) })); setCode(""); } catch (error) { setFormError(errorMessage(error, "Pairing failed. Check that the code is fresh and try again.")); } finally { setPairing(false); }
  }
  function chooseCredentialProvider(provider: string) {
    setCredentialProvider(provider);
    const preset = credentialPresets.find((item) => item.value === provider);
    setCredentialName(preset?.name || "");
  }
  async function saveCredential() {
    if (!credentialName.trim() || !credentialSecret) return;
    setCredentialBusy(true);
    setFormError(null);
    try {
      setCredentials(await desktop.saveCredential(credentialName.trim().toUpperCase(), credentialProvider, credentialSecret));
      setCredentialSecret("");
    } catch (error) { setFormError(errorMessage(error, "Could not save local credential")); }
    finally { setCredentialBusy(false); }
  }
  async function removeCredential(name: string) {
    setCredentialBusy(true);
    try { setCredentials(await desktop.deleteCredential(name)); }
    catch (error) { setFormError(errorMessage(error, "Could not remove local credential")); }
    finally { setCredentialBusy(false); }
  }
  return <SettingsPanel connection={connection} apiUrl={apiUrl} webUrl={webUrl} workspace={workspace} modelProfile={modelProfile} roots={roots} terminal={terminal} domains={domains} code={code} pairing={pairing} credentials={credentials} localModelProfiles={localModelProfiles} credentialProvider={credentialProvider} credentialName={credentialName} credentialSecret={credentialSecret} credentialBusy={credentialBusy} formError={formError} setApiUrl={setApiUrl} setWebUrl={setWebUrl} setWorkspace={setWorkspace} setModelProfile={setModelProfile} setRoots={setRoots} setTerminal={setTerminal} setDomains={setDomains} setCode={setCode} setFormError={setFormError} setCredentialName={setCredentialName} setCredentialSecret={setCredentialSecret} save={save} pair={pair} chooseCredentialProvider={chooseCredentialProvider} saveCredential={saveCredential} removeCredential={removeCredential} onSignIn={onSignIn} onLocalModelsChanged={setLocalModelProfiles} />;
  return <section className="content-page settings-page"><div className="page-intro"><div><span className="eyebrow">DESKTOP CONFIGURATION</span><h2>Settings</h2><p>Keep the local boundary clear. The hosted agent can ask; this PC decides what is allowed.</p></div><Button kind="primary" onClick={() => void save()} disabled={!isNativeDesktop}>Save changes</Button></div>{formError && <div className="form-error" role="alert"><span>!</span><span>{formError}</span><button onClick={() => setFormError(null)} aria-label="Dismiss error">×</button></div>}{!isNativeDesktop && <div className="callout preview-callout"><span>i</span><div><strong>Desktop UI preview</strong><p>Settings and executor actions become active in the installed Windows app.</p></div></div>}<div className="settings-grid"><div className="settings-card"><div className="card-heading"><span className="card-icon">◉</span><div><h3>Hosted connection</h3><p>One hosted brain for chat, planning, memory and task control.</p></div></div><label>Smara Web URL<input value={webUrl} onChange={(event) => { setFormError(null); setWebUrl(event.target.value); }} spellCheck={false} /></label><label>Smara API URL<input value={apiUrl} onChange={(event) => { setFormError(null); setApiUrl(event.target.value); }} spellCheck={false} /></label><label>Workspace name<input value={workspace} onChange={(event) => setWorkspace(event.target.value)} /></label><label>Hosted model<select value={modelProfile} onChange={(event) => setModelProfile(event.target.value)}>{modelProfiles.map((profile) => <option value={profile.value} key={profile.value}>{profile.label}</option>)}</select></label><p className="small-help">Grok and Sarvam keys remain on the hosted Smara service. This app sends only the selected profile name.</p><div className="connection-help"><span className={`connection-check ${connection.has_cli_token ? "check-on" : ""}`}>{connection.has_cli_token ? "✓" : "i"}</span><span>{connection.has_cli_token ? "This desktop is signed in. Chat and task history are available." : "Sign in once to use hosted chat and task history from this app."}</span>{!connection.has_cli_token && <Button kind="quiet" onClick={() => void (async () => { const saved = await save(); if (saved) onSignIn(saved.api_url, saved.web_url); })()} disabled={!isNativeDesktop}>Sign in ↗</Button>}</div></div><div className="settings-card"><div className="card-heading"><span className="card-icon">⌁</span><div><h3>Pair this desktop</h3><p>Paste the one-time code shown in Smara Web.</p></div></div><div className="pair-status"><span className={`status-dot ${connection.paired ? "dot-green" : "dot-amber"}`} /><strong>{connection.paired ? "Paired and scoped" : "Not paired"}</strong>{connection.executor_id && <span>{connection.executor_id}</span>}</div><div className="pair-row"><input value={code} onChange={(event) => { setFormError(null); setCode(event.target.value.toUpperCase()); }} placeholder="8-character code" maxLength={8} spellCheck={false} /><Button kind="primary" onClick={() => void pair()} disabled={!isNativeDesktop || pairing || code.trim().length !== 8}>{pairing ? "Pairing…" : "Pair device"}</Button></div><p className="small-help">Open Smara Web → Settings → Desktop → Pair device. Codes expire quickly and can be used only once.</p></div><div className="settings-card full-card"><div className="card-heading"><span className="card-icon">◇</span><div><h3>Local tool credentials</h3><p>Encrypted for your Windows account. Values never go to the VM and are never shown again.</p></div></div><div className="credential-entry"><label>Tool<select value={credentialProvider} onChange={(event) => chooseCredentialProvider(event.target.value)}>{credentialPresets.map((preset) => <option value={preset.value} key={preset.value}>{preset.label}</option>)}</select></label><label>Environment name<input value={credentialName} onChange={(event) => setCredentialName(event.target.value.toUpperCase())} placeholder="TOOL_API_KEY" spellCheck={false} /></label><label>Secret value<input type="password" value={credentialSecret} onChange={(event) => setCredentialSecret(event.target.value)} placeholder="Paste locally" autoComplete="off" /></label><Button kind="primary" onClick={() => void saveCredential()} disabled={!isNativeDesktop || credentialBusy || !credentialName.trim() || !credentialSecret}>{credentialBusy ? "Saving…" : "Save locally"}</Button></div><div className="credential-list">{credentials.length === 0 ? <span className="credential-empty">No personal tool credentials saved on this PC.</span> : credentials.map((credential) => <div className="credential-row" key={credential.name}><span className="credential-provider">{credential.provider}</span><strong>{credential.name}</strong><span>••••••••</span><Button kind="quiet" onClick={() => void removeCredential(credential.name)} disabled={credentialBusy}>Remove</Button></div>)}</div><div className="permission-note"><span>▣</span><span>An approved local terminal step may request an alias through <code>credential_env</code>. Smara injects it only for that process and redacts the value from returned output.</span></div></div><div className="settings-card full-card"><div className="card-heading"><span className="card-icon">⌂</span><div><h3>Local permissions</h3><p>One entry per line. Empty means the capability stays disabled.</p></div></div><div className="permission-grid"><label>Approved folders<textarea value={roots} onChange={(event) => setRoots(event.target.value)} placeholder={'C:\\Users\\you\\Documents'} /></label><label>Terminal executables<textarea value={terminal} onChange={(event) => setTerminal(event.target.value)} placeholder={'python\ngit'} /></label><label>Browser domains<textarea value={domains} onChange={(event) => setDomains(event.target.value)} placeholder={'github.com\nexample.com'} /></label></div><div className="permission-note"><span>▣</span><span>Smara rejects shell operators, path traversal, symlink escapes, unknown executables, unapproved domains, and unapproved tasks. Changing these lists never grants a task approval.</span></div></div><div className="settings-card full-card about-card"><div><span className="eyebrow">ABOUT THIS APP</span><h3>Thin client, one Smara brain</h3><p>This app does not run a second agent or memory database. It keeps the executor responsive on your PC while the hosted Smara service handles chat, planning, research, and durable task state.</p></div><div className="version">v0.1 beta<br /><span>Windows native</span></div></div></div></section>;
}

type SettingsPanelProps = {
  connection: ConnectionState;
  apiUrl: string; webUrl: string; workspace: string; modelProfile: string;
  roots: string; terminal: string; domains: string; code: string; pairing: boolean;
  credentials: LocalCredentialSummary[]; localModelProfiles: LocalModelProfile[]; credentialProvider: string; credentialName: string; credentialSecret: string; credentialBusy: boolean; formError: string | null;
  setApiUrl: (value: string) => void; setWebUrl: (value: string) => void; setWorkspace: (value: string) => void; setModelProfile: (value: string) => void;
  setRoots: (value: string) => void; setTerminal: (value: string) => void; setDomains: (value: string) => void; setCode: (value: string) => void; setFormError: (value: string | null) => void;
  setCredentialName: (value: string) => void; setCredentialSecret: (value: string) => void;
  save: (selectedModel?: string) => Promise<ConnectionState | null>; pair: () => Promise<void>; chooseCredentialProvider: (provider: string) => void; saveCredential: () => Promise<void>; removeCredential: (name: string) => Promise<void>; onSignIn: (apiUrl: string, webUrl: string) => void; onLocalModelsChanged: (profiles: LocalModelProfile[]) => void;
};

function SettingsPanel({ connection, apiUrl, webUrl, workspace, modelProfile, roots, terminal, domains, code, pairing, credentials, localModelProfiles, credentialProvider, credentialName, credentialSecret, credentialBusy, formError, setApiUrl, setWebUrl, setWorkspace, setModelProfile, setRoots, setTerminal, setDomains, setCode, setFormError, setCredentialName, setCredentialSecret, save, pair, chooseCredentialProvider, saveCredential, removeCredential, onSignIn, onLocalModelsChanged }: SettingsPanelProps) {
  const localSelected = modelProfile.startsWith("local:") ? localModelProfiles.find((profile) => `local:${profile.id}` === modelProfile) : undefined;
  const selectedProfile = localSelected ? { label: localSelected.label, provider: `${localSelected.provider} · local`, description: `Private desktop chat via ${localSelected.model}.`, tone: "amber" } : modelProfiles.find((profile) => profile.value === modelProfile) || modelProfiles[0];
  const selectedCredential = credentialPresets.find((preset) => preset.value === credentialProvider) || credentialPresets[0];
  const hasCredential = (provider: string, name: string) => credentials.some((credential) => credential.provider === provider || credential.name === name);
  const permissionSummary = [
    { icon: "▣", label: "Files", value: splitLines(roots).length, detail: "approved folder" },
    { icon: "⌘", label: "Terminal", value: splitLines(terminal).length, detail: "allowed executable" },
    { icon: "◌", label: "Browser", value: splitLines(domains).length, detail: "approved domain" },
  ];
  type SettingsSection = "connection" | "models" | "tools" | "permissions";
  const [activeSection, setActiveSection] = useState<SettingsSection>("connection");
  const [modelDialogOpen, setModelDialogOpen] = useState(false);
  const [modelProvider, setModelProvider] = useState("sarvam");
  const [modelId, setModelId] = useState("sarvam");
  const [modelLabel, setModelLabel] = useState("Sarvam");
  const [modelEndpoint, setModelEndpoint] = useState("https://api.sarvam.ai/v1/chat/completions");
  const [modelName, setModelName] = useState("sarvam-105b");
  const [modelAuthHeader, setModelAuthHeader] = useState("api-subscription-key");
  const [modelKey, setModelKey] = useState("");
  const [modelBusy, setModelBusy] = useState(false);
  function selectModel(value: string) {
    setModelProfile(value);
    void save(value);
  }
  function applyModelPreset(provider: string) {
    setModelProvider(provider);
    if (provider === "sarvam") { setModelId("sarvam"); setModelLabel("Sarvam"); setModelEndpoint("https://api.sarvam.ai/v1/chat/completions"); setModelName("sarvam-105b"); setModelAuthHeader("api-subscription-key"); }
    else if (provider === "grok") { setModelId("grok"); setModelLabel("Grok"); setModelEndpoint("https://api.x.ai/v1/chat/completions"); setModelName("grok-3-mini"); setModelAuthHeader("authorization"); }
    else { setModelId("custom"); setModelLabel("Custom model"); setModelEndpoint(""); setModelName(""); setModelAuthHeader("authorization"); }
  }
  async function saveLocalModel() {
    if (!modelKey.trim() || !modelEndpoint.trim() || !modelName.trim()) { setFormError("Enter an endpoint, model name, and API key."); return; }
    setModelBusy(true); setFormError(null);
    try {
      const profiles = await desktop.saveModelProfile({ id: modelId, label: modelLabel, provider: modelProvider, base_url: modelEndpoint, model: modelName, api_key: modelKey, auth_header: modelAuthHeader });
      const selectedModel = `local:${modelId}`;
      onLocalModelsChanged(profiles); setModelProfile(selectedModel); await save(selectedModel); setModelKey(""); setModelDialogOpen(false);
    } catch (error) { setFormError(errorMessage(error, "Could not save local model profile")); }
    finally { setModelBusy(false); }
  }
  async function removeLocalModel(id: string) {
    setModelBusy(true); setFormError(null);
    try {
      onLocalModelsChanged(await desktop.deleteModelProfile(id));
      if (modelProfile === `local:${id}`) { setModelProfile("default"); await save("default"); }
    }
    catch (error) { setFormError(errorMessage(error, "Could not remove local model profile")); }
    finally { setModelBusy(false); }
  }
  return <section className="content-page settings-page">
    <div className="page-intro"><div><span className="eyebrow">DESKTOP CONFIGURATION</span><h2>Settings</h2><p>Choose the hosted brain, then decide exactly what this PC may do. Nothing runs locally until you pair and start the executor.</p></div><Button kind="primary" onClick={() => void save()} disabled={!isNativeDesktop}>Save changes</Button></div>
    {formError && <div className="form-error" role="alert"><span>!</span><span>{formError}</span><button onClick={() => setFormError(null)} aria-label="Dismiss error">×</button></div>}
    {!isNativeDesktop && <div className="callout preview-callout"><span>i</span><div><strong>Desktop UI preview</strong><p>Settings and executor actions become active in the installed Windows app.</p></div></div>}
    <nav className="settings-tabs" aria-label="Settings sections">
      {([['connection', 'Connection', 'Hosted access & pairing'], ['models', 'Models', 'Hosted and private providers'], ['tools', 'Tools & credentials', 'Local keys and skills'], ['permissions', 'Permissions', 'Local execution boundaries']] as const).map(([value, label, description]) => <button type="button" key={value} className={`settings-tab ${activeSection === value ? 'settings-tab-active' : ''}`} onClick={() => setActiveSection(value)} aria-pressed={activeSection === value}><strong>{label}</strong><small>{description}</small></button>)}
    </nav>
    <div className="settings-grid" data-active-section={activeSection}>
      <div className="settings-card hosted-card"><div className="card-heading"><span className="card-icon">◉</span><div><h3>Hosted connection</h3><p>One Smara brain for chat, planning, memory and task control.</p></div><span className="card-badge">Hosted</span></div>
        <label>Smara Web URL<input value={webUrl} onChange={(event) => { setFormError(null); setWebUrl(event.target.value); }} spellCheck={false} /></label>
        <label>Smara API URL<input value={apiUrl} onChange={(event) => { setFormError(null); setApiUrl(event.target.value); }} spellCheck={false} /></label>
        <div className="inline-fields"><label>Workspace<input value={workspace} onChange={(event) => setWorkspace(event.target.value)} /></label><label>Profile name<input value={modelProfile} onChange={(event) => setModelProfile(event.target.value)} spellCheck={false} /></label></div>
        <div className="connection-help"><span className={`connection-check ${connection.has_cli_token ? "check-on" : ""}`}>{connection.has_cli_token ? "✓" : "i"}</span><span>{connection.has_cli_token ? "Signed in. Hosted chat and task history are ready." : "Sign in once to use hosted chat and task history."}</span>{!connection.has_cli_token && <Button kind="quiet" onClick={() => void (async () => { const saved = await save(); if (saved) onSignIn(saved.api_url, saved.web_url); })()} disabled={!isNativeDesktop}>Sign in ↗</Button>}</div>
      </div>
      <div className="settings-card"><div className="card-heading"><span className="card-icon">⌁</span><div><h3>Pair this desktop</h3><p>Link this PC to your Smara account with a one-time code.</p></div></div><div className="pair-status"><span className={`status-dot ${connection.paired ? "dot-green" : "dot-amber"}`} /><strong>{connection.paired ? "Paired and scoped" : "Not paired"}</strong>{connection.executor_id && <span>{connection.executor_id}</span>}</div><div className="pair-row"><input value={code} onChange={(event) => { setFormError(null); setCode(normalizePairingCode(event.target.value)); }} onPaste={(event) => { event.preventDefault(); setFormError(null); setCode(normalizePairingCode(event.clipboardData.getData("text"))); }} placeholder="8-character code" maxLength={32} inputMode="text" autoComplete="one-time-code" spellCheck={false} aria-describedby="pairing-help" /><Button kind="primary" onClick={() => void pair()} disabled={!isNativeDesktop || pairing || normalizePairingCode(code).length !== 8}>{pairing ? "Pairing…" : "Pair device"}</Button></div><p className="small-help" id="pairing-help">{code ? `${normalizePairingCode(code).length}/8 characters · ` : ""}Paste the code from Smara Web. Spaces and line breaks are removed automatically; codes are single-use and expire in 10 minutes.</p></div>
      <div className="settings-card full-card model-card"><div className="card-heading"><span className="card-icon">✦</span><div><h3>Model provider</h3><p>Use the hosted Smara brain, or add a private provider for direct desktop chat. API keys saved here are encrypted to this Windows account and never uploaded.</p></div><span className={`model-selected model-${selectedProfile.tone}`}>{selectedProfile.label}</span></div><div className="provider-grid" role="radiogroup" aria-label="Hosted model profile">{modelProfiles.map((profile) => <button type="button" className={`provider-option ${modelProfile === profile.value ? "provider-selected" : ""}`} key={profile.value} onClick={() => selectModel(profile.value)} aria-pressed={modelProfile === profile.value}><span className={`provider-mark provider-${profile.tone}`}>{profile.label.slice(0, 1)}</span><span className="provider-copy"><strong>{profile.label}</strong><small>{profile.provider}</small><span>{profile.description}</span></span><span className="provider-check">{modelProfile === profile.value ? "✓" : ""}</span></button>)}</div><div className="small-help model-help"><span>Selected:</span> {selectedProfile.label} · {selectedProfile.description} Hosted profiles use operator-configured keys; local profiles use only this PC.</div><div className="local-model-heading"><div><span className="eyebrow">PRIVATE DESKTOP PROVIDERS</span><p>Connect Sarvam, Grok, or any OpenAI-compatible endpoint without sending its key to Smara.</p></div><Button kind="quiet" onClick={() => { applyModelPreset("sarvam"); setModelDialogOpen(true); }} disabled={!isNativeDesktop}>＋ Add provider</Button></div>{localModelProfiles.length === 0 ? <div className="local-model-empty">No private model profiles yet. Add one to chat directly from this PC.</div> : <div className="local-model-list">{localModelProfiles.map((profile) => <div className={`local-model-row ${modelProfile === `local:${profile.id}` ? "local-model-selected" : ""}`} key={profile.id}><button type="button" className="local-model-select" onClick={() => selectModel(`local:${profile.id}`)}><span className="provider-mark provider-amber">{profile.label.slice(0, 1)}</span><span><strong>{profile.label}</strong><small>{profile.provider} · {profile.model}</small></span>{modelProfile === `local:${profile.id}` && <b>Selected</b>}</button><button type="button" className="local-model-remove" onClick={() => void removeLocalModel(profile.id)} disabled={modelBusy}>Remove</button></div>)}</div>}
        {modelDialogOpen && <div className="modal-backdrop" role="presentation"><div className="provider-dialog" role="dialog" aria-modal="true" aria-labelledby="provider-dialog-title"><div className="dialog-heading"><div><span className="eyebrow">LOCAL MODEL</span><h3 id="provider-dialog-title">Add a provider</h3><p>Your key stays encrypted on this PC. This private profile is for direct desktop chat; hosted task planning still uses the hosted profile.</p></div><button type="button" onClick={() => setModelDialogOpen(false)} aria-label="Close">×</button></div><label>Provider<select value={modelProvider} onChange={(event) => applyModelPreset(event.target.value)}><option value="sarvam">Sarvam AI</option><option value="grok">Grok (xAI)</option><option value="custom">Custom OpenAI-compatible</option></select></label><div className="inline-fields"><label>Profile name<input value={modelLabel} onChange={(event) => setModelLabel(event.target.value)} placeholder="Sarvam" /></label><label>Profile id<input value={modelId} onChange={(event) => setModelId(event.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, "-"))} placeholder="sarvam" /></label></div><label>Chat endpoint<input value={modelEndpoint} onChange={(event) => setModelEndpoint(event.target.value)} placeholder="https://api.sarvam.ai/v1/chat/completions" spellCheck={false} /></label><label>Model name<input value={modelName} onChange={(event) => setModelName(event.target.value)} placeholder="sarvam-105b" spellCheck={false} /></label><label>Key header<select value={modelAuthHeader} onChange={(event) => setModelAuthHeader(event.target.value)}><option value="api-subscription-key">api-subscription-key (Sarvam)</option><option value="authorization">Authorization: Bearer (Grok/OpenAI-compatible)</option></select></label><label>API key<input type="password" value={modelKey} onChange={(event) => setModelKey(event.target.value)} placeholder="Paste locally — never uploaded" autoComplete="off" /></label><div className="dialog-actions"><Button kind="quiet" onClick={() => setModelDialogOpen(false)}>Cancel</Button><Button kind="primary" onClick={() => void saveLocalModel()} disabled={modelBusy || !modelKey.trim()}>{modelBusy ? "Saving…" : "Save securely"}</Button></div></div></div>}
      </div>
      <div className="settings-card full-card credential-card"><div className="card-heading"><span className="card-icon">◇</span><div><h3>Tools &amp; credentials</h3><p>Choose a local tool and add only the credential it needs. Secrets stay encrypted on this Windows account and never upload.</p></div><span className="card-badge badge-private">Local only</span></div><div className="credential-status-grid"><button type="button" className={`credential-status ${hasCredential("tavily", "TAVILY_API_KEY") ? "status-configured" : ""} ${credentialProvider === "tavily" ? "status-selected" : ""}`} onClick={() => chooseCredentialProvider("tavily")} aria-pressed={credentialProvider === "tavily"}><span className="credential-status-icon">⌕</span><span><strong>Tavily Search</strong><small>{hasCredential("tavily", "TAVILY_API_KEY") ? "Configured on this PC" : "Not configured"}</small></span></button><button type="button" className={`credential-status ${hasCredential("github", "GITHUB_TOKEN") ? "status-configured" : ""} ${credentialProvider === "github" ? "status-selected" : ""}`} onClick={() => chooseCredentialProvider("github")} aria-pressed={credentialProvider === "github"}><span className="credential-status-icon">◈</span><span><strong>GitHub</strong><small>{hasCredential("github", "GITHUB_TOKEN") ? "Configured on this PC" : "Not configured"}</small></span></button><button type="button" className={`credential-status ${credentialProvider === "custom" ? "status-selected" : ""}`} onClick={() => chooseCredentialProvider("custom")} aria-pressed={credentialProvider === "custom"}><span className="credential-status-icon">＋</span><span><strong>Custom tool</strong><small>{credentials.filter((credential) => credential.provider === "custom").length ? `${credentials.filter((credential) => credential.provider === "custom").length} saved locally` : "Optional"}</small></span></button></div><div className="credential-selected"><span className="eyebrow">ADDING A CREDENTIAL FOR {selectedCredential.label.toUpperCase()}</span><span>{selectedCredential.description} Use the exact environment variable expected by this tool.</span></div><div className="credential-entry"><label>Tool<select value={credentialProvider} onChange={(event) => chooseCredentialProvider(event.target.value)}>{credentialPresets.map((preset) => <option value={preset.value} key={preset.value}>{preset.label}</option>)}</select></label><label>Environment variable<input aria-label="Environment variable name" value={credentialName} onChange={(event) => setCredentialName(event.target.value.toUpperCase())} placeholder="TOOL_API_KEY" spellCheck={false} /></label><label>Secret value<input aria-label={`Secret value for ${selectedCredential.label}`} type="password" value={credentialSecret} onChange={(event) => setCredentialSecret(event.target.value)} placeholder="Paste locally — never uploaded" autoComplete="off" /></label><Button kind="primary" onClick={() => void saveCredential()} disabled={!isNativeDesktop || credentialBusy || !credentialName.trim() || !credentialSecret}>{credentialBusy ? "Saving…" : "Save locally"}</Button></div><div className="credential-list">{credentials.length === 0 ? <span className="credential-empty">No local credentials saved. Hosted provider keys are managed separately.</span> : credentials.map((credential) => <div className="credential-row" key={credential.name}><span className="credential-provider">{credential.provider}</span><strong>{credential.name}</strong><span>••••••••</span><Button kind="quiet" onClick={() => void removeCredential(credential.name)} disabled={credentialBusy}>Remove</Button></div>)}</div><div className="permission-note"><span>▣</span><span>Smara injects a selected alias only into the approved process and redacts it from output. It never sends this value to the hosted service.</span></div></div>
      <div className="settings-card full-card permissions-card"><div className="card-heading"><span className="card-icon">⌂</span><div><h3>Local permissions</h3><p>These are the hard boundaries for files, terminal, and browser work. Empty means disabled.</p></div><span className="card-badge">Approval still required</span></div><div className="permission-summary">{permissionSummary.map((item) => <div className={`permission-summary-item ${item.value ? "summary-enabled" : ""}`} key={item.label}><span className="permission-summary-icon">{item.icon}</span><div><strong>{item.label}</strong><span>{item.value ? `${item.value} ${item.detail}${item.value === 1 ? "" : "s"}` : "Disabled"}</span></div><span className="permission-state">{item.value ? "On" : "Off"}</span></div>)}</div><div className="permission-grid"><label><span>Approved folders <em>{splitLines(roots).length} entries</em></span><textarea value={roots} onChange={(event) => setRoots(event.target.value)} placeholder={'C:\\Users\\you\\Documents'} /></label><label><span>Terminal executables <em>{splitLines(terminal).length} entries</em></span><textarea value={terminal} onChange={(event) => setTerminal(event.target.value)} placeholder={'python\ngit'} /></label><label><span>Browser domains <em>{splitLines(domains).length} entries</em></span><textarea value={domains} onChange={(event) => setDomains(event.target.value)} placeholder={'github.com\nexample.com'} /></label></div><div className="permission-note"><span>▣</span><span>Shell operators, path traversal, symlink escapes, unknown executables, unapproved domains, and unapproved tasks are rejected. Lists define eligibility; they never approve a task.</span></div></div>
      <div className="settings-card full-card about-card"><div><span className="eyebrow">ABOUT THIS APP</span><h3>Thin client, one Smara brain</h3><p>This app keeps files, browser sessions, credentials, and terminal work on your PC. The hosted Smara service handles chat, planning, research, and durable task state.</p></div><div className="version">v0.1 beta<br /><span>Windows native</span></div></div>
    </div>
  </section>;
}

export default App;
