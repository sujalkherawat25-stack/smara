import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode, type RefObject } from "react";
import { desktop, isNativeDesktop } from "./api";
import type { ActivityItem, ChatEvent, ChatMessage, ConnectionState, LocalConnectorSummary, LocalCredentialSummary, LocalModelProfile, Screen, TaskDetail, TaskSummary } from "./types";
import smaraLogo from "./assets/smara-logo.svg";

const fallbackConnection: ConnectionState = {
  runtime_mode: "local",
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
  auto_approve_safe: false,
  approval_mode: "ask",
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

function initialConversationId() {
  // Local mode uses the private transcript journal in the native companion,
  // so keep one stable id across app restarts. Hosted sessions continue to
  // receive a fresh id per process; the shared Syntarus memory write now
  // carries continuity across those conversations without risking a stale
  // id being reused after an account switch.
  try {
    const existing = window.localStorage.getItem("smara.local.conversation_id");
    if (existing) return existing;
    const value = `local-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
    window.localStorage.setItem("smara.local.conversation_id", value);
    return value;
  } catch {
    return `local-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
  }
}

function localConversationId() {
  try {
    const existing = window.localStorage.getItem("smara.local.conversation_id");
    if (existing) return existing;
    const value = `local-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
    window.localStorage.setItem("smara.local.conversation_id", value);
    return value;
  } catch {
    return `local-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
  }
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

function firstJsonObject(value: string): Record<string, unknown> | null {
  const start = value.indexOf("{");
  if (start < 0) return null;
  let depth = 0;
  let quoted = false;
  let escaped = false;
  for (let index = start; index < value.length; index += 1) {
    const character = value[index];
    if (quoted) {
      if (escaped) escaped = false;
      else if (character === "\\") escaped = true;
      else if (character === '"') quoted = false;
      continue;
    }
    if (character === '"') quoted = true;
    else if (character === "{") depth += 1;
    else if (character === "}") {
      depth -= 1;
      if (depth === 0) {
        try {
          const parsed: unknown = JSON.parse(value.slice(start, index + 1));
          return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed as Record<string, unknown> : null;
        } catch { return null; }
      }
    }
  }
  return null;
}

function taskActivitySummary(task: TaskSummary) {
  const raw = task.result?.trim();
  if (!raw) return task.objective;
  const result = firstJsonObject(raw);
  if (!result || typeof result.action !== "string") return raw;
  const action = result.action.replace(/^local_/, "").replaceAll("_", " ");
  if (result.action === "local_file_read") {
    const file = typeof result.file_name === "string" ? result.file_name : "approved file";
    const bytes = typeof result.bytes_read === "number" ? ` · ${result.bytes_read} B` : "";
    return `Read ${file} locally${bytes}.`;
  }
  if (result.action === "local_browser") {
    const url = typeof result.url === "string" ? result.url : "approved site";
    return `Opened ${url} in the local browser.`;
  }
  if (result.action === "local_terminal") {
    const recipe = typeof result.recipe === "string" ? ` (${result.recipe})` : "";
    const exit = typeof result.exit_code === "number" ? ` · exit ${result.exit_code}` : "";
    return `Ran local terminal${recipe}${exit}.`;
  }
  return `Completed local ${action}.`;
}

function Icon({ children }: { children: string }) {
  return <span className="icon" aria-hidden="true">{children}</span>;
}

function Button({ children, onClick, kind = "secondary", disabled = false }: { children: ReactNode; onClick?: () => void; kind?: "primary" | "secondary" | "quiet" | "danger"; disabled?: boolean }) {
  return <button className={`button button-${kind}`} onClick={onClick} disabled={disabled}>{children}</button>;
}

function StatusPill({ connection }: { connection: ConnectionState }) {
  if (connection.runtime_mode === "local") {
    return <span className="status-pill status-green"><span className="status-dot" />Local mode</span>;
  }
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
  const pendingAssistantText = useRef("");
  const durableWatchIds = useRef(new Set<string>());
  const assistantFrame = useRef<number | null>(null);
  const conversationId = useRef(initialConversationId());
  const transcriptEndRef = useRef<HTMLDivElement>(null!);
  const chatEventHandler = useRef<(event: ChatEvent) => void>(() => undefined);

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
      if (next.runtime_mode === "local") {
        setRemote({ ok: true, detail: "Local mode — hosted sign-in is optional" });
      } else try {
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
      const message = errorMessage(error, connection.runtime_mode === "local" ? "Local tasks could not be loaded" : "Sign in to see hosted tasks");
      if (message.includes("401")) {
        setConnection((current) => ({ ...current, has_cli_token: false }));
        setNotice("Your Smara sign-in expired. Sign in again to load chat and hosted tasks.");
      } else {
        setNotice(message);
      }
    }
  }, [connection.runtime_mode]);

  useEffect(() => {
    void (async () => {
      const next = await refreshConnection();
      setLoading(false);
      if (next?.runtime_mode === "local" || next?.has_cli_token) await refreshTasks();
    })();
  }, [refreshConnection, refreshTasks]);

  useEffect(() => {
    // A stable local transcript id must never be sent to a different hosted
    // account. Rotate it when the runtime is hosted and restore it whenever
    // the user switches back to local mode.
    if (connection.runtime_mode === "local") {
      if (!conversationId.current.startsWith("local-")) conversationId.current = localConversationId();
    } else if (conversationId.current.startsWith("local-")) {
      conversationId.current = `desktop-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    }
  }, [connection.runtime_mode]);

  useEffect(() => {
    if (screen !== "activity" || (!connection.has_cli_token && connection.runtime_mode !== "local") || !isNativeDesktop) return;
    const timer = window.setInterval(() => {
      void Promise.all([refreshTasks(), refreshConnection()]);
    }, 5000);
    return () => window.clearInterval(timer);
  }, [screen, connection.has_cli_token, refreshConnection, refreshTasks]);

  const flushAssistantText = useCallback(() => {
    if (assistantFrame.current !== null) {
      window.cancelAnimationFrame(assistantFrame.current);
      assistantFrame.current = null;
    }
    const target = assistantId.current;
    if (!target) return;
    const text = pendingAssistantText.current;
    setMessages((items) => items.map((item) => item.id === target ? { ...item, text, pending: true } : item));
  }, []);

  const queueAssistantText = useCallback((text: string) => {
    pendingAssistantText.current = text;
    if (assistantFrame.current !== null) return;
    assistantFrame.current = window.requestAnimationFrame(() => {
      assistantFrame.current = null;
      const target = assistantId.current;
      if (!target) return;
      setMessages((items) => items.map((item) => item.id === target ? { ...item, text: pendingAssistantText.current, pending: true } : item));
    });
  }, []);

  const appendDurableResult = useCallback((taskId: string, result: string) => {
    const parsed = firstJsonObject(result);
    const text = parsed ? JSON.stringify(parsed, null, 2) : result.trim();
    if (!text) return;
    setMessages((items) => {
      if (items.some((item) => item.id === `task-result-${taskId}`)) return items;
      return [...items, { id: `task-result-${taskId}`, role: "assistant", text }];
    });
    setActivity((items) => [{ id: uid("task-result"), tone: "green" as const, label: "Local task result received", detail: "The paired desktop returned the completed result." }, ...items].slice(0, 8));
  }, []);

  const watchDurableTask = useCallback(async (parentTaskId: string) => {
    if (!isNativeDesktop || durableWatchIds.current.has(parentTaskId)) return;
    durableWatchIds.current.add(parentTaskId);
    let childTaskId: string | null = parentTaskId.startsWith("local_") ? parentTaskId : null;
      const deadline = Date.now() + 10 * 60 * 1000;
    try {
      while (Date.now() < deadline) {
        const requestedTaskId: string = childTaskId || parentTaskId;
        const detail = await desktop.taskDetails(requestedTaskId);
        const task = detail.task;
        if (!childTaskId) {
          for (const event of detail.events || []) {
            if (event.type !== "agent.desktop_task_requested" && event.type !== "agent.desktop_workflow_requested") continue;
            try {
              const payload = JSON.parse(event.payload || "{}");
              if (typeof payload.task_id === "string" && payload.task_id) { childTaskId = payload.task_id; break; }
            } catch { /* malformed event is ignored; parent remains visible */ }
          }
        }
        // The parent event that reveals the desktop child arrives in the
        // parent response. Start the next poll against that child rather than
        // accidentally treating the parent's planning result as local output.
        if (childTaskId && requestedTaskId !== childTaskId) {
          await new Promise((resolve) => window.setTimeout(resolve, 200));
          continue;
        }
        if (childTaskId && task.status === "completed" && typeof task.result === "string" && task.result.trim()) {
          appendDurableResult(childTaskId, task.result);
          return;
        }
        if (childTaskId && ["failed", "cancelled"].includes(task.status)) {
          setActivity((items) => [{ id: uid("task-result-error"), tone: "red" as const, label: `Local task ${task.status}`, detail: task.result || "The desktop task did not complete." }, ...items].slice(0, 8));
          return;
        }
        await new Promise((resolve) => window.setTimeout(resolve, 2000));
      }
      setActivity((items) => [{ id: uid("task-result-timeout"), tone: "amber" as const, label: "Local task still running", detail: "Open Activity to see the latest status." }, ...items].slice(0, 8));
    } catch (error) {
      setActivity((items) => [{ id: uid("task-result-error"), tone: "red" as const, label: "Could not load local result", detail: errorMessage(error, "Open Activity to retry.") }, ...items].slice(0, 8));
    } finally {
      durableWatchIds.current.delete(parentTaskId);
    }
  }, [appendDurableResult]);

  const handleChatEvent = useCallback((event: ChatEvent) => {
    const target = assistantId.current;
    if (!target) return;
    const type = event.type || "status";
    if (type === "token" && event.text) {
      pendingAssistantText.current += event.text;
      queueAssistantText(pendingAssistantText.current);
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
      const approvalRequired = /created\s+Smara\s+task|approval-gated/i.test(pendingAssistantText.current);
      flushAssistantText();
      setStreaming(false);
      setMessages((items) => items.map((item) => item.id === target ? { ...item, pending: false } : item));
      setActivity((items) => [
        ...(approvalRequired ? [{ id: uid("approval"), tone: "amber" as const, label: "Desktop task queued", detail: "Safe reads follow the Desktop policy; writes and terminal work still require approval." }] : []),
        { id: uid("done"), tone: "green" as const, label: "Response ready", detail: event.total_ms ? `${event.total_ms} ms` : undefined },
        ...items,
      ].slice(0, 8));
      if (event.task_id) void watchDurableTask(event.task_id);
      assistantId.current = null;
    } else if (type === "error") {
      flushAssistantText();
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
      setActivity((items) => [{ id: uid("error"), tone: "red" as const, label: connection.runtime_mode === "local" ? "Local response failed" : "Hosted response failed", detail }, ...items].slice(0, 8));
      assistantId.current = null;
    }
  }, [connection.runtime_mode, flushAssistantText, queueAssistantText, watchDurableTask]);

  // Keep one native listener for the lifetime of the app. The Tauri listen
  // call is asynchronous; re-registering it whenever connection state changes
  // can leave the old listener alive and duplicate every token/event.
  chatEventHandler.current = handleChatEvent;

  useEffect(() => {
    if (!isNativeDesktop) return;
    let unlisten: (() => void) | undefined;
    let disposed = false;
    void desktop.onChatEvent((event) => chatEventHandler.current(event)).then((dispose) => {
      if (disposed) dispose();
      else unlisten = dispose;
    }).catch((error) => {
      if (!disposed) setNotice(errorMessage(error, "Live chat events could not be connected."));
    });
    return () => {
      disposed = true;
      unlisten?.();
    };
  }, []);

  async function send(message = draft) {
    const text = message.trim();
    if (!text || streaming) return;
    if (!isNativeDesktop) {
      setNotice("This is the browser preview. Open the installed Smara Desktop app to chat and run local work.");
      return;
    }
    const localModelSelected = connection.model_profile.startsWith("local:");
    if (connection.runtime_mode === "local" && !localModelSelected) {
      setScreen("settings");
      setNotice("Local mode is ready. Choose a private desktop model in Settings before starting a chat.");
      return;
    }
    if (connection.runtime_mode !== "local" && !connection.has_cli_token && !localModelSelected) {
      setScreen("settings");
      setNotice("Sign in to Smara, or add a private model in Settings before starting a chat.");
      return;
    }
    setDraft("");
    const answerId = uid("assistant");
    assistantId.current = answerId;
    pendingAssistantText.current = "";
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
  const loadTaskDetails = useCallback((taskId: string) => taskId.startsWith("local_") ? desktop.localTaskDetails(taskId) : desktop.taskDetails(taskId), []);

  return <div className="app-shell">
    <aside className="sidebar">
      <div className="brand"><div className="brand-mark"><img src={smaraLogo} alt="" /></div><div><div className="brand-name">Smara</div><div className="brand-sub">desktop companion</div></div></div>
      <div className="workspace-switch"><span className="workspace-dot" /><div><span className="workspace-label">Workspace</span><strong>{connection.workspace}</strong></div><span className="chevron">⌄</span></div>
      <nav className="nav" aria-label="Main navigation">
        {navItems.map(([value, label, icon]) => <button key={value} className={`nav-item ${screen === value ? "nav-active" : ""}`} onClick={() => setScreen(value)}><Icon>{icon}</Icon>{label}{value === "activity" && tasks.some((task) => task.status === "waiting_approval" && task.approval_mode === "desktop") && <span className="nav-badge">!</span>}</button>)}
      </nav>
      <div className="sidebar-bottom"><div className="privacy-note"><span className="lock">▣</span><div><strong>Private by design</strong><span>Files, browser and terminal stay here.</span></div></div><button className="help-link" onClick={() => void desktop.openWeb()}>Open Smara Web <span>↗</span></button></div>
    </aside>

    <main className="main-panel">
      <header className="topbar"><div className="topbar-title"><span className="eyebrow">LOCAL COMPANION</span><h1>{screen === "chat" ? "Chat" : screen === "activity" ? "Activity" : "Settings"}</h1></div><div className="topbar-actions">{isNativeDesktop && connection.runtime_mode === "cloud" && connection.paired && !connection.running && !connection.paused && <Button kind="primary" onClick={() => void runAction(desktop.start, "Desktop executor started. Approved local work can now be claimed.")}>Start executor</Button>}<StatusPill connection={connection} />{isNativeDesktop && connection.runtime_mode === "cloud" && !connection.has_cli_token && <Button kind="quiet" onClick={() => void signIn()} disabled={signingIn}>{signingIn ? "Signing in…" : "Sign in"}</Button>}<span className={`remote-pill ${remote?.ok ? "remote-online" : ""}`}><span className="status-dot" />{!isNativeDesktop ? "UI preview" : connection.runtime_mode === "local" ? "Local · cloud optional" : remote?.ok ? connection.has_cli_token ? "Hosted connected" : "Hosted ready · sign in" : loading ? "Checking…" : "Hosted offline"}</span><button className="icon-button" onClick={() => void refreshConnection()} aria-label="Refresh connection">↻</button></div></header>
      {notice && <div className="notice" role="status"><span>i</span>{notice}<button onClick={() => setNotice(null)} aria-label="Dismiss">×</button></div>}
      {screen === "chat" && <ChatScreen messages={messages} draft={draft} setDraft={setDraft} onSend={() => void send()} onStarter={(value) => void send(value)} streaming={streaming} activity={activity} canChat={(connection.runtime_mode === "local" ? connection.model_profile.startsWith("local:") : connection.has_cli_token || connection.model_profile.startsWith("local:"))} localModel={connection.model_profile.startsWith("local:")} connection={connection} onStartExecutor={() => void runAction(desktop.start, "Desktop executor started. Approved local work can now be claimed.")} onSignIn={() => setScreen("settings")} onOpenWeb={() => void desktop.openWeb()} onClearActivity={() => setActivity([])} transcriptEndRef={transcriptEndRef} />}
      {screen === "activity" && <ActivityScreen connection={connection} tasks={tasks} onLoadTaskDetails={loadTaskDetails} onRefresh={() => void Promise.all([refreshTasks(), refreshConnection()])} onStart={() => void runAction(desktop.start, "Desktop executor started.")} onStop={() => void runAction(desktop.stop, "Desktop executor stopped.")} onPause={() => void runAction(desktop.pause, "Desktop executor paused.")} onResume={() => void runAction(desktop.resume, "Desktop executor resumed.")} onRevoke={() => { if (window.confirm("Revoke this desktop? Approved local work will stop and you will need to pair again.")) void runAction(desktop.revoke, "Desktop executor revoked. Pair again to reconnect."); }} onOpenWeb={() => void desktop.openWeb()} onReadLog={() => desktop.log()} />}
      {screen === "settings" && <SettingsScreen connection={connection} onSaved={(next) => { setConnection(next); setNotice("Desktop settings saved."); }} onPaired={(next) => { setConnection(next); setNotice(next.runtime_mode === "local" ? "Desktop paired for optional cloud use. Local mode stays active." : "Desktop paired. Start the executor when you are ready."); }} onSignIn={(apiUrl, webUrl) => void signIn(apiUrl, webUrl)} />}
    </main>
  </div>;
}

function ChatScreen({ messages, draft, setDraft, onSend, onStarter, streaming, activity, canChat, localModel, connection, onStartExecutor, onSignIn, onOpenWeb, onClearActivity, transcriptEndRef }: { messages: ChatMessage[]; draft: string; setDraft: (value: string) => void; onSend: () => void; onStarter: (value: string) => void; streaming: boolean; activity: ActivityItem[]; canChat: boolean; localModel: boolean; connection: ConnectionState; onStartExecutor: () => void; onSignIn: () => void; onOpenWeb: () => void; onClearActivity: () => void; transcriptEndRef: RefObject<HTMLDivElement> }) {
  const stickToBottomRef = useRef(true);
  const executorStopped = isNativeDesktop && connection.runtime_mode === "cloud" && connection.paired && !connection.running && !connection.paused;
  const [showJumpToLatest, setShowJumpToLatest] = useState(false);

  const handleTranscriptScroll = () => {
    const el = transcriptEndRef.current?.parentElement;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 96;
    stickToBottomRef.current = atBottom;
    setShowJumpToLatest(!atBottom && el.scrollHeight > el.clientHeight);
  };

  useEffect(() => {
    // Sending a new user turn should reveal the latest context. Once the
    // assistant is streaming, respect a user who has scrolled up to read.
    if (messages[messages.length - 1]?.role === "user") stickToBottomRef.current = true;
    if (!stickToBottomRef.current) return;
    const frame = window.requestAnimationFrame(() => {
      const el = transcriptEndRef.current?.parentElement;
      if (!el) return;
      el.scrollTo({ top: el.scrollHeight, behavior: streaming ? "auto" : "smooth" });
      setShowJumpToLatest(false);
    });
    return () => window.cancelAnimationFrame(frame);
  }, [messages, streaming, transcriptEndRef]);

  return <section className="chat-layout"><div className={`chat-column ${messages.length > 0 ? "has-messages" : ""}`}><div className="hero"><div className="hero-orb"><img src={smaraLogo} alt="Smara" /></div><span className="eyebrow">SMARA WORKSPACE</span><h2>What are we working on?</h2><p>{localModel ? "Private chat stays on this PC. Switch to a hosted profile for task planning and live task history." : "Ask the hosted agent anything. Approved local work comes back to this desktop."}</p>{executorStopped && <div className="chat-auth-card"><strong>Local execution is stopped</strong><span>Chat and planning still work. Start the executor before approving a task that needs this PC; until then it will wait safely without running.</span><Button kind="primary" onClick={onStartExecutor}>Start executor</Button></div>}{isNativeDesktop && !canChat && <div className="chat-auth-card"><strong>{connection.runtime_mode === "local" ? "Choose a private model to start" : "Sign in or add a model to start"}</strong><span>{connection.runtime_mode === "local" ? "Local-first mode does not need hosted sign-in. Add a private provider in Settings; its key stays on this PC." : "Connect this desktop to hosted Smara, or add a private provider in Settings. Your local permissions stay on this PC."}</span><Button kind="primary" onClick={onSignIn}>Open Settings ↗</Button></div>}</div>{messages.length === 0 ? <div className="starter-grid">{starterPrompts.map((prompt) => <button className="starter" key={prompt} onClick={() => onStarter(prompt)} disabled={!canChat}><span>{prompt}</span><span className="arrow">↗</span></button>)}</div> : <div className="transcript" aria-live="polite" aria-label="Conversation" tabIndex={0} onScroll={handleTranscriptScroll}>{messages.map((message) => <div className={`message-row message-${message.role}`} key={message.id}><div className="avatar">{message.role === "user" ? "S" : <img src={smaraLogo} alt="" />}</div><div className={`message ${message.failed ? "message-failed" : ""}`}>{message.text || (message.pending ? <span className="typing"><i /><i /><i /></span> : "")}{message.pending && message.text && <span className="cursor" />}{message.error && message.text && <span className="message-error">{message.error}</span>}</div></div>)}<div ref={transcriptEndRef} aria-hidden="true" /></div>}{showJumpToLatest && <button type="button" className="jump-latest" onClick={() => { stickToBottomRef.current = true; const el = transcriptEndRef.current?.parentElement; el?.scrollTo({ top: el.scrollHeight, behavior: "smooth" }); setShowJumpToLatest(false); }}>↓ Newest</button>}<div className="composer-wrap"><div className="composer"><textarea value={draft} onChange={(event) => setDraft(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); onSend(); } }} placeholder={canChat ? localModel ? "Message your private model…" : "Message Smara…" : connection.runtime_mode === "local" ? "Add a private model in Settings…" : "Sign in or add a model to start…"} rows={1} disabled={streaming || !canChat} /><button className="send-button" onClick={onSend} disabled={!draft.trim() || streaming || !canChat} aria-label="Send message">↑</button></div><div className="composer-hint"><span>Enter to send</span><span>Shift + Enter for a new line</span><span className="model-hint">{localModel ? "Private model · direct from this PC" : "Hosted agent · local actions approval-gated"}</span></div></div></div><aside className="live-rail"><div className="rail-heading"><span>Live activity</span><div className="rail-heading-actions"><span className={streaming ? "pulse" : ""}>{streaming ? "working" : "ready"}</span>{activity.length > 0 && <button type="button" className="rail-clear" onClick={onClearActivity}>Clear</button>}</div></div>{activity.length === 0 ? <div className="rail-empty"><div className="rail-icon">◌</div><strong>Your work appears here</strong><p>Tool calls, approvals and local execution stay visible without interrupting chat.</p></div> : <div className="activity-list">{activity.map((item) => <div className="activity-item" key={item.id}><span className={`activity-dot activity-${item.tone}`} /><div><strong>{item.label}</strong>{item.detail && <span>{item.detail}</span>}</div></div>)}</div>}<div className="rail-footer"><span className="lock">▣</span><span>Local files and credentials never leave this device.</span></div><Button kind="quiet" onClick={onOpenWeb}>Open full workspace ↗</Button></aside></section>;
}

function parseArtifactResult(content?: string | null): Record<string, unknown> | null {
  if (!content) return null;
  try {
    const value: unknown = JSON.parse(content);
    return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
  } catch {
    return null;
  }
}

function ArtifactResult({ artifact }: { artifact: TaskDetail["artifacts"][number] }) {
  const result = parseArtifactResult(artifact.content);
  const changedFiles = result && Array.isArray(result.changed_files) ? result.changed_files.filter((item): item is string => typeof item === "string") : [];
  const artifacts = result && Array.isArray(result.artifacts) ? result.artifacts.filter((item): item is Record<string, unknown> => Boolean(item && typeof item === "object" && !Array.isArray(item))) : [];
  const preview = result && result.preview && typeof result.preview === "object" && !Array.isArray(result.preview) ? result.preview as Record<string, unknown> : null;
  const documentResult = result && result.document && typeof result.document === "object" && !Array.isArray(result.document) ? result.document as Record<string, unknown> : null;
  const stageResult = result && result.stage_result && typeof result.stage_result === "object" && !Array.isArray(result.stage_result) ? result.stage_result as Record<string, unknown> : null;
  const diff = preview && typeof preview.diff === "string" ? preview.diff : null;
  if (!result) return <div className="detail-line artifact-row"><span>{artifact.name || artifact.kind || "artifact"}</span><span>{artifact.kind || ""}</span></div>;
  const action = typeof result.action === "string" ? result.action : artifact.kind || "local result";
  const recipe = typeof result.recipe === "string" ? result.recipe : null;
  const exitCode = typeof result.exit_code === "number" ? result.exit_code : null;
  const ok = exitCode === null || exitCode === 0;
  return <details className="artifact-result" open={action === "local_file_write" || action === "local_file_preview"}>
    <summary><span className={`artifact-status ${ok ? "artifact-ok" : "artifact-failed"}`} />{artifact.name || action}<span className="artifact-kind">{recipe || action}</span></summary>
    <div className="artifact-body">
      <div className="artifact-metrics">
        {recipe && <span>Recipe <strong>{recipe}</strong></span>}
        {exitCode !== null && <span>Exit <strong className={ok ? "" : "metric-failed"}>{exitCode}</strong></span>}
        {typeof result.sha256 === "string" && <span>SHA-256 <strong>{result.sha256.slice(0, 12)}…</strong></span>}
        {typeof result.undo_id === "string" && <span>Undo available <strong>locally</strong></span>}
      </div>
      {documentResult && <div className="artifact-subsection"><span className="eyebrow">LOCAL DOCUMENT</span><div className="artifact-chips">{Object.entries(documentResult).map(([key, value]) => <code key={key}>{key.replaceAll("_", " ")}: {Array.isArray(value) ? value.join(", ") : String(value)}</code>)}</div></div>}
      {stageResult && <div className="artifact-subsection"><span className="eyebrow">WORKSPACE STAGE</span><div className="artifact-chips"><code>{String(stageResult.stage || "stage")} · {String(stageResult.status || "completed")}</code>{typeof stageResult.summary === "string" && <code>{stageResult.summary}</code>}{Array.isArray(stageResult.acceptance) && stageResult.acceptance.slice(0, 12).map((check, index) => <code key={index}>{typeof check === "object" && check ? `${String((check as Record<string, unknown>).status || "pending")} — ${String((check as Record<string, unknown>).check || "acceptance check")}` : String(check)}</code>)}</div></div>}
      {changedFiles.length > 0 && <div className="artifact-subsection"><span className="eyebrow">CHANGED FILES</span><div className="artifact-chips">{changedFiles.slice(0, 100).map((file) => <code key={file}>{file}</code>)}</div></div>}
      {artifacts.length > 0 && <div className="artifact-subsection"><span className="eyebrow">TEST ARTIFACTS</span><div className="artifact-chips">{artifacts.slice(0, 20).map((item, index) => <code key={`${String(item.path || item.name || index)}`}>{String(item.path || item.name || "artifact")}{typeof item.bytes === "number" ? ` · ${item.bytes} B` : ""}</code>)}</div></div>}
      {diff && <div className="artifact-subsection"><span className="eyebrow">PREVIEW / DIFF</span><pre className="artifact-diff">{diff}</pre></div>}
      {typeof result.output === "string" && result.output && <div className="artifact-subsection"><span className="eyebrow">LOCAL OUTPUT</span><pre className="artifact-output">{result.output}</pre></div>}
      {!changedFiles.length && !artifacts.length && !documentResult && !stageResult && !diff && !result.output && <span className="detail-muted">Structured local result recorded. Open the full task history for the complete artifact.</span>}
    </div>
  </details>;
}

function workspaceJobFromTaskSteps(steps: TaskDetail["steps"]) {
  const step = steps.find((item) => item.workspace_job && typeof item.workspace_job === "object");
  return step?.workspace_job && typeof step.workspace_job === "object" ? step.workspace_job as Record<string, unknown> : null;
}

function ActivityScreen({ connection, tasks, onLoadTaskDetails, onRefresh, onStart, onStop, onPause, onResume, onRevoke, onOpenWeb, onReadLog, onDecideLocalTask }: { connection: ConnectionState; tasks: TaskSummary[]; onLoadTaskDetails: (taskId: string) => Promise<TaskDetail>; onRefresh: () => void; onStart: () => void; onStop: () => void; onPause: () => void; onResume: () => void; onRevoke: () => void; onOpenWeb: () => void; onReadLog: () => Promise<string>; onDecideLocalTask?: (taskId: string, approved: boolean) => Promise<void> }) {
  const [showLog, setShowLog] = useState(false);
  const [log, setLog] = useState("No local executor log yet.");
  const [loadingLog, setLoadingLog] = useState(false);
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [taskDetail, setTaskDetail] = useState<TaskDetail | null>(null);
  const [loadingTaskDetail, setLoadingTaskDetail] = useState(false);
  const [deciding, setDeciding] = useState(false);
  const waiting = tasks.filter((task) => task.status === "waiting_approval" && task.approval_mode === "desktop");
  const selectedTask = tasks.find((task) => task.id === selectedTaskId) || null;
  const workspaceJob = workspaceJobFromTaskSteps(taskDetail?.steps || []);
  useEffect(() => {
    if (!selectedTaskId) { setTaskDetail(null); return; }
    let active = true;
    setLoadingTaskDetail(true);
    void onLoadTaskDetails(selectedTaskId).then((detail) => { if (active) setTaskDetail(detail); }).catch(() => { if (active) setTaskDetail(null); }).finally(() => { if (active) setLoadingTaskDetail(false); });
    return () => { active = false; };
  }, [selectedTaskId, onLoadTaskDetails]);
  async function toggleLog() {
    if (showLog) { setShowLog(false); return; }
    setLoadingLog(true);
    try { setLog(await onReadLog()); }
    catch (error) { setLog(errorMessage(error, "The local executor log could not be read.")); }
    finally { setLoadingLog(false); setShowLog(true); }
  }
  async function decideLocal(approved: boolean) {
    if (!selectedTask || deciding) return;
    setDeciding(true);
    try { if (onDecideLocalTask) await onDecideLocalTask(selectedTask.id, approved); else await desktop.decideLocalTask(selectedTask.id, approved); await onRefresh(); }
    finally { setDeciding(false); }
  }
  useEffect(() => {
    const previous = document.getElementById("desktop-task-approval");
    previous?.remove();
    if (!selectedTask || selectedTask.approval_mode !== "desktop") return;
    const canApprove = ["waiting_approval", "review_required", "failed"].includes(selectedTask.status);
    const canCancel = ["queued", "running", "cancelling"].includes(selectedTask.status);
    if (!canApprove && !canCancel) return;
    const detail = document.querySelector(".task-detail");
    if (!detail) return;
    const panel = document.createElement("div");
    panel.id = "desktop-task-approval";
    panel.className = "callout callout-amber";
    const message = document.createElement("div");
    message.innerHTML = canApprove
      ? `<strong>${selectedTask.status === "waiting_approval" ? "Approve on this Desktop" : "Review before retry"}</strong><p>${selectedTask.status === "review_required" ? "The previous run was interrupted and will not replay automatically." : "This decision stays on this PC."}</p>`
      : "<strong>Local task is active</strong><p>Cancel requests are checked during bounded terminal, browser, and connector work.</p>";
    const approve = document.createElement("button"); approve.className = "button button-primary"; approve.textContent = deciding ? "Working…" : selectedTask.status === "waiting_approval" ? "Approve locally" : "Retry locally"; approve.disabled = deciding;
    const deny = document.createElement("button"); deny.className = "button button-quiet"; deny.textContent = "Reject"; deny.disabled = deciding;
    approve.onclick = () => { void decideLocal(true); };
    deny.onclick = () => { void decideLocal(false); };
    panel.append(message);
    if (canApprove) panel.append(approve);
    panel.append(deny);
    detail.insertBefore(panel, detail.children[2] || null);
    return () => panel.remove();
  }, [selectedTask?.id, selectedTask?.status, selectedTask?.approval_mode, deciding]);
  useEffect(() => {
    // Keep the local-only approval boundary clear in the shared task view.
    const callout = document.querySelector(".content-page > .callout.callout-amber");
    if (!callout || waiting.length === 0) return;
    const paragraph = callout.querySelector("p");
    if (paragraph) paragraph.textContent = "Approve or reject this work here on the paired Desktop. Smara Web can only show its status.";
    callout.querySelector("button")?.remove();
  }, [waiting.length]);
  const localMode = connection.runtime_mode === "local";
  return <section className="content-page"><div className="page-intro"><div><span className="eyebrow">CONTROL CENTER</span><h2>{localMode ? "Local workspace" : "Local activity"}</h2><p>{localMode ? "Private task state stays on this PC. Cloud pairing is optional." : "See what the hosted agent is asking this PC to do, and stop it at any time."}</p></div><Button onClick={onRefresh}>Refresh</Button></div><div className="executor-banner"><div className="executor-main"><div className={`executor-icon ${connection.running ? "executor-online" : ""}`}>⌘</div><div><span className="eyebrow">{localMode ? "LOCAL RUNTIME" : "PAIRED DEVICE"}</span><h3>{localMode ? "This desktop" : connection.paired ? "This desktop" : "No desktop paired"}</h3><p>{localMode ? "Local mode is active. Choose a private model in Settings to chat without sign-in." : connection.paired ? `${connection.executor_id} · ${connection.capabilities.length} capabilities declared` : "Pair this device in Settings to receive approved local work."}</p></div></div><div className="executor-actions">{!localMode && (connection.running ? <><Button onClick={connection.paused ? onResume : onPause}>{connection.paused ? "Resume" : "Pause"}</Button><Button kind="quiet" onClick={onStop}>Stop</Button></> : <Button kind="primary" onClick={onStart} disabled={!connection.paired}>Start executor</Button>)}</div></div>{waiting.length > 0 && <div className="callout callout-amber"><span>!</span><div><strong>{waiting.length} task{waiting.length === 1 ? "" : "s"} waiting for approval</strong><p>{localMode ? "Approve or reject this work on the Desktop. No hosted approval is required." : "Approve or reject this work here on the paired Desktop. Smara Web only shows its status."}</p></div>{!localMode && <Button kind="quiet" onClick={onOpenWeb}>Open Web ↗</Button>}</div>}<div className="section-heading"><h3>{localMode ? "Local tasks" : "Hosted tasks"}</h3><span>{tasks.length} total · refreshes automatically</span></div><div className="task-list">{tasks.length === 0 ? <div className="empty-state"><span>◌</span><strong>{localMode ? "No local tasks yet" : "No hosted tasks loaded"}</strong><p>{localMode ? "Ask the private Desktop model to read, create, edit, run, browse, or use a configured connector." : "Sign in to Smara, then return here. Activity refreshes automatically."}</p></div> : tasks.slice(0, 12).map((task) => <button className={`task-row ${selectedTaskId === task.id ? "task-row-selected" : ""}`} key={task.id} onClick={() => setSelectedTaskId(task.id === selectedTaskId ? null : task.id)}><span className={`task-dot task-${task.status}`} /><span className="task-copy"><strong>{task.title}</strong><span>{taskActivitySummary(task)}</span></span><span className={`task-status task-status-${task.status}`}>{task.status.replaceAll("_", " ")}</span><span className="task-time">{formatTime(task.updated_at || task.created_at)}</span></button>)}</div>{selectedTask && <div className="task-detail"><div><span className="eyebrow">TASK RESULT</span><h3>{selectedTask.title}</h3><p>{selectedTask.objective}</p></div><span className={`task-status task-status-${selectedTask.status}`}>{selectedTask.status.replaceAll("_", " ")}</span><div className="task-result">{loadingTaskDetail ? "Loading full task record…" : taskDetail?.task?.result || taskActivitySummary(selectedTask) || (selectedTask.status === "completed" ? "The task completed without a written result." : "A final result will appear here when the task completes.")}</div>{taskDetail && <><div className="task-detail-section"><span className="eyebrow">STEPS</span>{taskDetail.steps.length === 0 ? <span className="detail-muted">No steps recorded.</span> : taskDetail.steps.map((step, index) => <div className="detail-line" key={`${String(step.id || index)}`}><span className={`task-dot task-${String(step.status || "queued")}`} /><span>{String(step.name || `Step ${index + 1}`)}</span>{typeof step.stage === "string" && <span className="detail-stage">{step.stage}</span>}<span>{String(step.status || "queued")}{Number(step.attempt || 0) > 1 ? ` · attempt ${step.attempt}` : ""}</span></div>)}</div>{workspaceJob && <div className="task-detail-section workspace-run"><span className="eyebrow">WORKSPACE RUN</span><div className="workspace-run-grid"><span>Scope</span><strong>{String(workspaceJob.workspace_root || "approved workspace")}</strong><span>Isolation</span><strong>{String(workspaceJob.isolation || "none")}</strong><span>Repair budget</span><strong>{String((workspaceJob.budgets as Record<string, unknown> | undefined)?.max_repair_attempts ?? 0)} retries</strong></div>{Array.isArray(workspaceJob.acceptance_checks) && workspaceJob.acceptance_checks.length > 0 && <div className="workspace-checks">{(workspaceJob.acceptance_checks as unknown[]).slice(0, 12).map((check, index) => <span key={`${String(check)}-${index}`}>· {String(check)}</span>)}</div>}</div>}<div className="task-detail-section"><span className="eyebrow">ACTIVITY</span>{taskDetail.events.slice(-12).map((event, index) => <div className="detail-line" key={`${event.id || index}`}><span>{event.type || "task update"}</span><span>{event.created_at ? formatTime(event.created_at) : ""}</span></div>)}</div><div className="task-detail-section"><span className="eyebrow">ARTIFACTS</span>{taskDetail.artifacts.length === 0 ? <span className="detail-muted">No artifacts produced.</span> : <div className="artifact-list">{taskDetail.artifacts.map((artifact, index) => <ArtifactResult artifact={artifact} key={`${artifact.id || index}`} />)}</div>}</div></>}{!localMode && <Button kind="quiet" onClick={onOpenWeb}>Open full task history ↗</Button>}</div>}<div className="log-card"><div><strong>Local executor log</strong><span>Only the last bounded lines are shown; secrets are never displayed.</span></div><Button kind="quiet" onClick={() => void toggleLog()}>{loadingLog ? "Loading…" : showLog ? "Hide log" : "View log"}</Button>{showLog && <pre className="log-output">{log}</pre>}</div><div className="danger-zone"><div><strong>Revoke this desktop</strong><span>{localMode ? "Revoke hosted pairing only; local mode remains available." : "Immediately invalidates its paired token. You can pair again later."}</span></div><Button kind="danger" onClick={onRevoke} disabled={!connection.paired}>Revoke</Button></div></section>;
}

function SettingsScreen({ connection, onSaved, onPaired, onSignIn }: { connection: ConnectionState; onSaved: (next: ConnectionState) => void; onPaired: (next: ConnectionState) => void; onSignIn: (apiUrl: string, webUrl: string) => void }) {
  const [runtimeMode, setRuntimeMode] = useState<"local" | "cloud">(connection.runtime_mode || "local");
  const [apiUrl, setApiUrl] = useState(connection.api_url);
  const [webUrl, setWebUrl] = useState(connection.web_url);
  const [workspace, setWorkspace] = useState(connection.workspace);
  const [modelProfile, setModelProfile] = useState(connection.model_profile);
  const [roots, setRoots] = useState(connection.allowed_roots.join("\n"));
  const [terminal, setTerminal] = useState(connection.terminal_allowlist.join("\n"));
  const [domains, setDomains] = useState(connection.browser_domains.join("\n"));
  const [autoApproveSafe, setAutoApproveSafe] = useState(connection.auto_approve_safe);
  const [approvalMode, setApprovalMode] = useState<"ask" | "auto">(connection.approval_mode || "ask");
  const [code, setCode] = useState("");
  const [pairing, setPairing] = useState(false);
  const [credentials, setCredentials] = useState<LocalCredentialSummary[]>([]);
  const [connectors, setConnectors] = useState<LocalConnectorSummary[]>([]);
  const [localModelProfiles, setLocalModelProfiles] = useState<LocalModelProfile[]>([]);
  const [credentialProvider, setCredentialProvider] = useState("tavily");
  const [credentialName, setCredentialName] = useState("TAVILY_API_KEY");
  const [credentialSecret, setCredentialSecret] = useState("");
  const [credentialBusy, setCredentialBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  useEffect(() => { setRuntimeMode(connection.runtime_mode || "local"); setApiUrl(connection.api_url); setWebUrl(connection.web_url); setWorkspace(connection.workspace); setModelProfile(connection.model_profile); setRoots(connection.allowed_roots.join("\n")); setTerminal(connection.terminal_allowlist.join("\n")); setDomains(connection.browser_domains.join("\n")); setAutoApproveSafe(connection.auto_approve_safe); setApprovalMode(connection.approval_mode || "ask"); }, [connection]);
  useEffect(() => {
    if (!isNativeDesktop) return;
    void desktop.credentials().then(setCredentials).catch(() => setCredentials([]));
    void desktop.connectors().then(setConnectors).catch(() => setConnectors([]));
    void desktop.modelProfiles().then(setLocalModelProfiles).catch(() => setLocalModelProfiles([]));
  }, []);
  useEffect(() => {
    // Keep the policy control prominent even in older bundled layouts: the
    // selector is local-only and never grants the hosted service authority.
    const policy = document.querySelector(".approval-policy");
    const legacy = policy?.querySelector(".policy-toggle") as HTMLElement | null;
    if (!policy || !legacy) return;
    const description = policy.querySelector(":scope > div > span");
    if (description) description.textContent = "Ask before every local task, or let this paired Desktop approve declared capabilities for you. Smara Web remains status-only.";
    legacy.style.display = "none";
    const wrapper = document.createElement("label");
    wrapper.className = "policy-toggle desktop-policy-mode";
    wrapper.innerHTML = '<span>Local approval mode</span><select aria-label="Local approval mode"><option value="ask">Ask for approval</option><option value="auto">Approve for me</option></select>';
    const select = wrapper.querySelector("select") as HTMLSelectElement;
    select.value = approvalMode;
    const onChange = () => setApprovalMode(select.value === "auto" ? "auto" : "ask");
    select.addEventListener("change", onChange);
    policy.appendChild(wrapper);
    return () => { select.removeEventListener("change", onChange); wrapper.remove(); legacy.style.display = ""; };
  }, [approvalMode]);
  async function save(selectedModel = modelProfile) {
    if (!isNativeDesktop) return null;
    setFormError(null);
    try { const next = await desktop.saveSettings({ runtime_mode: runtimeMode, api_url: apiUrl.trim(), web_url: webUrl.trim(), workspace: workspace.trim() || "default", model_profile: selectedModel.trim() || "default", allowed_roots: splitLines(roots), terminal_allowlist: splitLines(terminal), browser_domains: splitLines(domains), auto_approve_safe: approvalMode === "auto" ? false : autoApproveSafe, approval_mode: approvalMode }); onSaved(next); return next; } catch (error) { setFormError(errorMessage(error, "Could not save settings")); return null; }
  }
  async function pair() {
    const normalizedCode = normalizePairingCode(code);
    if (normalizedCode.length !== 8) {
      setFormError(`Enter the complete 8-character pairing code (${normalizedCode.length}/8).`);
      return;
    }
    setPairing(true);
    setFormError(null);
    try { onPaired(await desktop.pair({ runtime_mode: runtimeMode, api_url: apiUrl.trim(), code: normalizedCode, allowed_roots: splitLines(roots), terminal_allowlist: splitLines(terminal), browser_domains: splitLines(domains), auto_approve_safe: approvalMode === "auto" ? false : autoApproveSafe, approval_mode: approvalMode })); setCode(""); } catch (error) { setFormError(errorMessage(error, "Pairing failed. Check that the code is fresh and try again.")); } finally { setPairing(false); }
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
      setConnectors(await desktop.connectors());
      setCredentialSecret("");
    } catch (error) { setFormError(errorMessage(error, "Could not save local credential")); }
    finally { setCredentialBusy(false); }
  }
  async function removeCredential(name: string) {
    setCredentialBusy(true);
    try { setCredentials(await desktop.deleteCredential(name)); setConnectors(await desktop.connectors()); }
    catch (error) { setFormError(errorMessage(error, "Could not remove local credential")); }
    finally { setCredentialBusy(false); }
  }
  async function revokeConnector(provider: string) {
    setCredentialBusy(true);
    try { setConnectors(await desktop.revokeConnector(provider)); setCredentials(await desktop.credentials()); }
    catch (error) { setFormError(errorMessage(error, "Could not disconnect local connector")); }
    finally { setCredentialBusy(false); }
  }
  return <SettingsPanel connection={connection} runtimeMode={runtimeMode} setRuntimeMode={setRuntimeMode} apiUrl={apiUrl} webUrl={webUrl} workspace={workspace} modelProfile={modelProfile} roots={roots} terminal={terminal} domains={domains} autoApproveSafe={autoApproveSafe} code={code} pairing={pairing} credentials={credentials} connectors={connectors} localModelProfiles={localModelProfiles} credentialProvider={credentialProvider} credentialName={credentialName} credentialSecret={credentialSecret} credentialBusy={credentialBusy} formError={formError} setApiUrl={setApiUrl} setWebUrl={setWebUrl} setWorkspace={setWorkspace} setModelProfile={setModelProfile} setRoots={setRoots} setTerminal={setTerminal} setDomains={setDomains} setAutoApproveSafe={setAutoApproveSafe} setCode={setCode} setFormError={setFormError} setCredentialName={setCredentialName} setCredentialSecret={setCredentialSecret} save={save} pair={pair} chooseCredentialProvider={chooseCredentialProvider} saveCredential={saveCredential} removeCredential={removeCredential} revokeConnector={revokeConnector} onSignIn={onSignIn} onLocalModelsChanged={setLocalModelProfiles} />;
  return <section className="content-page settings-page"><div className="page-intro"><div><span className="eyebrow">DESKTOP CONFIGURATION</span><h2>Settings</h2><p>Keep the local boundary clear. The hosted agent can ask; this PC decides what is allowed.</p></div><Button kind="primary" onClick={() => void save()} disabled={!isNativeDesktop}>Save changes</Button></div>{formError && <div className="form-error" role="alert"><span>!</span><span>{formError}</span><button onClick={() => setFormError(null)} aria-label="Dismiss error">×</button></div>}{!isNativeDesktop && <div className="callout preview-callout"><span>i</span><div><strong>Desktop UI preview</strong><p>Settings and executor actions become active in the installed Windows app.</p></div></div>}<div className="settings-grid"><div className="settings-card"><div className="card-heading"><span className="card-icon">◉</span><div><h3>Hosted connection</h3><p>One hosted brain for chat, planning, memory and task control.</p></div></div><label>Smara Web URL<input value={webUrl} onChange={(event) => { setFormError(null); setWebUrl(event.target.value); }} spellCheck={false} /></label><label>Smara API URL<input value={apiUrl} onChange={(event) => { setFormError(null); setApiUrl(event.target.value); }} spellCheck={false} /></label><label>Workspace name<input value={workspace} onChange={(event) => setWorkspace(event.target.value)} /></label><label>Hosted model<select value={modelProfile} onChange={(event) => setModelProfile(event.target.value)}>{modelProfiles.map((profile) => <option value={profile.value} key={profile.value}>{profile.label}</option>)}</select></label><p className="small-help">Grok and Sarvam keys remain on the hosted Smara service. This app sends only the selected profile name.</p><div className="connection-help"><span className={`connection-check ${connection.has_cli_token ? "check-on" : ""}`}>{connection.has_cli_token ? "✓" : "i"}</span><span>{connection.has_cli_token ? "This desktop is signed in. Chat and task history are available." : "Sign in once to use hosted chat and task history from this app."}</span>{!connection.has_cli_token && <Button kind="quiet" onClick={() => void (async () => { const saved = await save(); if (saved) onSignIn(saved.api_url, saved.web_url); })()} disabled={!isNativeDesktop}>Sign in ↗</Button>}</div></div><div className="settings-card"><div className="card-heading"><span className="card-icon">⌁</span><div><h3>Pair this desktop</h3><p>Paste the one-time code shown in Smara Web.</p></div></div><div className="pair-status"><span className={`status-dot ${connection.paired ? "dot-green" : "dot-amber"}`} /><strong>{connection.paired ? "Paired and scoped" : "Not paired"}</strong>{connection.executor_id && <span>{connection.executor_id}</span>}</div><div className="pair-row"><input value={code} onChange={(event) => { setFormError(null); setCode(event.target.value.toUpperCase()); }} placeholder="8-character code" maxLength={8} spellCheck={false} /><Button kind="primary" onClick={() => void pair()} disabled={!isNativeDesktop || pairing || code.trim().length !== 8}>{pairing ? "Pairing…" : "Pair device"}</Button></div><p className="small-help">Open Smara Web → Settings → Desktop → Pair device. Codes expire quickly and can be used only once.</p></div><div className="settings-card full-card"><div className="card-heading"><span className="card-icon">◇</span><div><h3>Local tool credentials</h3><p>Encrypted for your Windows account. Values never go to the VM and are never shown again.</p></div></div><div className="credential-entry"><label>Tool<select value={credentialProvider} onChange={(event) => chooseCredentialProvider(event.target.value)}>{credentialPresets.map((preset) => <option value={preset.value} key={preset.value}>{preset.label}</option>)}</select></label><label>Environment name<input value={credentialName} onChange={(event) => setCredentialName(event.target.value.toUpperCase())} placeholder="TOOL_API_KEY" spellCheck={false} /></label><label>Secret value<input type="password" value={credentialSecret} onChange={(event) => setCredentialSecret(event.target.value)} placeholder="Paste locally" autoComplete="off" /></label><Button kind="primary" onClick={() => void saveCredential()} disabled={!isNativeDesktop || credentialBusy || !credentialName.trim() || !credentialSecret}>{credentialBusy ? "Saving…" : "Save locally"}</Button></div><div className="credential-list">{credentials.length === 0 ? <span className="credential-empty">No personal tool credentials saved on this PC.</span> : credentials.map((credential) => <div className="credential-row" key={credential.name}><span className="credential-provider">{credential.provider}</span><strong>{credential.name}</strong><span>••••••••</span><Button kind="quiet" onClick={() => void removeCredential(credential.name)} disabled={credentialBusy}>Remove</Button></div>)}</div><div className="permission-note"><span>▣</span><span>An approved local terminal step may request an alias through <code>credential_env</code>. Smara injects it only for that process and redacts the value from returned output.</span></div></div><div className="settings-card full-card"><div className="card-heading"><span className="card-icon">⌂</span><div><h3>Local permissions</h3><p>Approved folders enable bounded text edits and local DOCX, XLSX, PPTX, and PDF creation. One entry per line; empty means file work stays disabled.</p></div></div><div className="permission-grid"><label>Approved folders<textarea value={roots} onChange={(event) => setRoots(event.target.value)} placeholder={'C:\\Users\\you\\Documents'} /></label><label>Terminal executables<textarea value={terminal} onChange={(event) => setTerminal(event.target.value)} placeholder={'python\ngit'} /></label><label>Browser domains<textarea value={domains} onChange={(event) => setDomains(event.target.value)} placeholder={'github.com\nexample.com'} /></label></div><div className="permission-note"><span>▣</span><span>Smara rejects shell operators, path traversal, symlink escapes, unknown executables, unapproved domains, and unapproved tasks. Document macros, scripts, and external links are not generated. Changing these lists never grants a task approval.</span></div></div><div className="settings-card full-card about-card"><div><span className="eyebrow">ABOUT THIS APP</span><h3>Thin client, one Smara brain</h3><p>This app does not run a second agent or memory database. It keeps the executor responsive on your PC while the hosted Smara service handles chat, planning, research, and durable task state.</p></div><div className="version">v0.1 beta<br /><span>Windows native</span></div></div></div></section>;
}

type SettingsPanelProps = {
  connection: ConnectionState;
  runtimeMode: "local" | "cloud"; setRuntimeMode: (value: "local" | "cloud") => void;
  apiUrl: string; webUrl: string; workspace: string; modelProfile: string;
  roots: string; terminal: string; domains: string; autoApproveSafe: boolean; code: string; pairing: boolean;
  credentials: LocalCredentialSummary[]; connectors: LocalConnectorSummary[]; localModelProfiles: LocalModelProfile[]; credentialProvider: string; credentialName: string; credentialSecret: string; credentialBusy: boolean; formError: string | null;
  setApiUrl: (value: string) => void; setWebUrl: (value: string) => void; setWorkspace: (value: string) => void; setModelProfile: (value: string) => void;
  setRoots: (value: string) => void; setTerminal: (value: string) => void; setDomains: (value: string) => void; setAutoApproveSafe: (value: boolean) => void; setCode: (value: string) => void; setFormError: (value: string | null) => void;
  setCredentialName: (value: string) => void; setCredentialSecret: (value: string) => void;
  save: (selectedModel?: string) => Promise<ConnectionState | null>; pair: () => Promise<void>; chooseCredentialProvider: (provider: string) => void; saveCredential: () => Promise<void>; removeCredential: (name: string) => Promise<void>; revokeConnector: (provider: string) => Promise<void>; onSignIn: (apiUrl: string, webUrl: string) => void; onLocalModelsChanged: (profiles: LocalModelProfile[]) => void;
};

function SettingsPanel({ connection, runtimeMode, setRuntimeMode, apiUrl, webUrl, workspace, modelProfile, roots, terminal, domains, autoApproveSafe, code, pairing, credentials, connectors, localModelProfiles, credentialProvider, credentialName, credentialSecret, credentialBusy, formError, setApiUrl, setWebUrl, setWorkspace, setModelProfile, setRoots, setTerminal, setDomains, setAutoApproveSafe, setCode, setFormError, setCredentialName, setCredentialSecret, save, pair, chooseCredentialProvider, saveCredential, removeCredential, revokeConnector, onSignIn, onLocalModelsChanged }: SettingsPanelProps) {
  const localSelected = modelProfile.startsWith("local:") ? localModelProfiles.find((profile) => `local:${profile.id}` === modelProfile) : undefined;
  const selectedProfile = localSelected ? { label: localSelected.label, provider: `${localSelected.provider} · local`, description: `Private desktop chat via ${localSelected.model}.`, tone: "amber" } : modelProfiles.find((profile) => profile.value === modelProfile) || modelProfiles[0];
  const selectedCredential = credentialPresets.find((preset) => preset.value === credentialProvider) || credentialPresets[0];
  const hasCredential = (provider: string, name: string) => credentials.some((credential) => credential.provider === provider || credential.name === name);
  const localIntegrationPaired = connection.capabilities.includes("local_integration");
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
    <div className="page-intro"><div><span className="eyebrow">DESKTOP CONFIGURATION</span><h2>Settings</h2><p>{runtimeMode === "local" ? "Run private chat and local work from this PC. Cloud pairing is optional." : "Connect the hosted coordinator for durable planning and cloud task history."}</p></div><Button kind="primary" onClick={() => void save()} disabled={!isNativeDesktop}>Save changes</Button></div>
    {formError && <div className="form-error" role="alert"><span>!</span><span>{formError}</span><button onClick={() => setFormError(null)} aria-label="Dismiss error">×</button></div>}
    {!isNativeDesktop && <div className="callout preview-callout"><span>i</span><div><strong>Desktop UI preview</strong><p>Settings and executor actions become active in the installed Windows app.</p></div></div>}
    <div className="runtime-mode-card"><div><span className="eyebrow">RUNTIME MODE</span><h3>{runtimeMode === "local" ? "Local-first" : "Cloud coordinated"}</h3><p>{runtimeMode === "local" ? "Private models, task state, approvals, and local tools stay on this PC. No hosted sign-in is needed." : "Hosted Smara provides durable task history, research, scheduling, and optional Desktop execution."}</p></div><label><span>Use Smara as</span><select value={runtimeMode} onChange={(event) => setRuntimeMode(event.target.value === "cloud" ? "cloud" : "local")}><option value="local">Local-first Desktop</option><option value="cloud">Hosted + Desktop</option></select></label></div>
    <nav className="settings-tabs" aria-label="Settings sections">
      {([['connection', 'Connection', 'Hosted access & pairing'], ['models', 'Models', 'Hosted and private providers'], ['tools', 'Tools & credentials', 'Local keys and skills'], ['permissions', 'Permissions', 'Local execution boundaries']] as const).map(([value, label, description]) => <button type="button" key={value} className={`settings-tab ${activeSection === value ? 'settings-tab-active' : ''}`} onClick={() => setActiveSection(value)} aria-pressed={activeSection === value}><strong>{label}</strong><small>{description}</small></button>)}
    </nav>
    <div className="settings-grid" data-active-section={activeSection}>
       <div className="settings-card hosted-card"><div className="card-heading"><span className="card-icon">◉</span><div><h3>{runtimeMode === "local" ? "Optional cloud connection" : "Hosted connection"}</h3><p>{runtimeMode === "local" ? "Keep these details ready if you later want cloud planning or durable task history." : "One Smara brain for chat, planning, memory and task control."}</p></div><span className="card-badge">{runtimeMode === "local" ? "Optional" : "Hosted"}</span></div>
        <label>Smara Web URL<input value={webUrl} onChange={(event) => { setFormError(null); setWebUrl(event.target.value); }} spellCheck={false} /></label>
        <label>Smara API URL<input value={apiUrl} onChange={(event) => { setFormError(null); setApiUrl(event.target.value); }} spellCheck={false} /></label>
        <div className="inline-fields"><label>Workspace<input value={workspace} onChange={(event) => setWorkspace(event.target.value)} /></label><label>Profile name<input value={modelProfile} onChange={(event) => setModelProfile(event.target.value)} spellCheck={false} /></label></div>
         <div className="connection-help"><span className={`connection-check ${connection.has_cli_token ? "check-on" : ""}`}>{connection.has_cli_token ? "✓" : "i"}</span><span>{connection.has_cli_token ? "Signed in. Hosted chat and task history are ready." : runtimeMode === "local" ? "Cloud sign-in is optional while Local-first mode is active." : "Sign in once to use hosted chat and task history."}</span>{runtimeMode === "cloud" && !connection.has_cli_token && <Button kind="quiet" onClick={() => void (async () => { const saved = await save(); if (saved) onSignIn(saved.api_url, saved.web_url); })()} disabled={!isNativeDesktop}>Sign in ↗</Button>}</div>
      </div>
       <div className="settings-card"><div className="card-heading"><span className="card-icon">⌁</span><div><h3>{runtimeMode === "local" ? "Optional cloud pairing" : "Pair this desktop"}</h3><p>{runtimeMode === "local" ? "Pair only when you want hosted tasks to reach this PC." : "Link this PC to your Smara account with a one-time code."}</p></div></div><div className="pair-status"><span className={`status-dot ${connection.paired ? "dot-green" : "dot-amber"}`} /><strong>{connection.paired ? "Paired and scoped" : "Not paired"}</strong>{connection.executor_id && <span>{connection.executor_id}</span>}</div><div className="pair-row"><input value={code} onChange={(event) => { setFormError(null); setCode(normalizePairingCode(event.target.value)); }} onPaste={(event) => { event.preventDefault(); setFormError(null); setCode(normalizePairingCode(event.clipboardData.getData("text"))); }} placeholder="8-character code" maxLength={32} inputMode="text" autoComplete="one-time-code" spellCheck={false} aria-describedby="pairing-help" /><Button kind="primary" onClick={() => void pair()} disabled={!isNativeDesktop || pairing || normalizePairingCode(code).length !== 8}>{pairing ? "Pairing…" : "Pair device"}</Button></div><p className="small-help" id="pairing-help">{code ? `${normalizePairingCode(code).length}/8 characters · ` : ""}Paste the code from Smara Web. Spaces and line breaks are removed automatically; codes are single-use and expire in 10 minutes.</p></div>
      <div className="settings-card full-card model-card"><div className="card-heading"><span className="card-icon">✦</span><div><h3>Model provider</h3><p>Use the hosted Smara brain, or add a private provider for direct desktop chat. API keys saved here are encrypted to this Windows account and never uploaded.</p></div><span className={`model-selected model-${selectedProfile.tone}`}>{selectedProfile.label}</span></div><div className="provider-grid" role="radiogroup" aria-label="Hosted model profile">{modelProfiles.map((profile) => <button type="button" className={`provider-option ${modelProfile === profile.value ? "provider-selected" : ""}`} key={profile.value} onClick={() => selectModel(profile.value)} aria-pressed={modelProfile === profile.value}><span className={`provider-mark provider-${profile.tone}`}>{profile.label.slice(0, 1)}</span><span className="provider-copy"><strong>{profile.label}</strong><small>{profile.provider}</small><span>{profile.description}</span></span><span className="provider-check">{modelProfile === profile.value ? "✓" : ""}</span></button>)}</div><div className="small-help model-help"><span>Selected:</span> {selectedProfile.label} · {selectedProfile.description} Hosted profiles use operator-configured keys; local profiles use only this PC.</div><div className="local-model-heading"><div><span className="eyebrow">PRIVATE DESKTOP PROVIDERS</span><p>Connect Sarvam, Grok, or any OpenAI-compatible endpoint without sending its key to Smara.</p></div><Button kind="quiet" onClick={() => { applyModelPreset("sarvam"); setModelDialogOpen(true); }} disabled={!isNativeDesktop}>＋ Add provider</Button></div>{localModelProfiles.length === 0 ? <div className="local-model-empty">No private model profiles yet. Add one to chat directly from this PC.</div> : <div className="local-model-list">{localModelProfiles.map((profile) => <div className={`local-model-row ${modelProfile === `local:${profile.id}` ? "local-model-selected" : ""}`} key={profile.id}><button type="button" className="local-model-select" onClick={() => selectModel(`local:${profile.id}`)}><span className="provider-mark provider-amber">{profile.label.slice(0, 1)}</span><span><strong>{profile.label}</strong><small>{profile.provider} · {profile.model}</small></span>{modelProfile === `local:${profile.id}` && <b>Selected</b>}</button><button type="button" className="local-model-remove" onClick={() => void removeLocalModel(profile.id)} disabled={modelBusy}>Remove</button></div>)}</div>}
        {modelDialogOpen && <div className="modal-backdrop" role="presentation"><div className="provider-dialog" role="dialog" aria-modal="true" aria-labelledby="provider-dialog-title"><div className="dialog-heading"><div><span className="eyebrow">LOCAL MODEL</span><h3 id="provider-dialog-title">Add a provider</h3><p>Your key stays encrypted on this PC. This private profile is for direct desktop chat; hosted task planning still uses the hosted profile.</p></div><button type="button" onClick={() => setModelDialogOpen(false)} aria-label="Close">×</button></div><label>Provider<select value={modelProvider} onChange={(event) => applyModelPreset(event.target.value)}><option value="sarvam">Sarvam AI</option><option value="grok">Grok (xAI)</option><option value="custom">Custom OpenAI-compatible</option></select></label><div className="inline-fields"><label>Profile name<input value={modelLabel} onChange={(event) => setModelLabel(event.target.value)} placeholder="Sarvam" /></label><label>Profile id<input value={modelId} onChange={(event) => setModelId(event.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, "-"))} placeholder="sarvam" /></label></div><label>Chat endpoint<input value={modelEndpoint} onChange={(event) => setModelEndpoint(event.target.value)} placeholder="https://api.sarvam.ai/v1/chat/completions" spellCheck={false} /></label><label>Model name<input value={modelName} onChange={(event) => setModelName(event.target.value)} placeholder="sarvam-105b" spellCheck={false} /></label><label>Key header<select value={modelAuthHeader} onChange={(event) => setModelAuthHeader(event.target.value)}><option value="api-subscription-key">api-subscription-key (Sarvam)</option><option value="authorization">Authorization: Bearer (Grok/OpenAI-compatible)</option></select></label><label>API key<input type="password" value={modelKey} onChange={(event) => setModelKey(event.target.value)} placeholder="Paste locally — never uploaded" autoComplete="off" /></label><div className="dialog-actions"><Button kind="quiet" onClick={() => setModelDialogOpen(false)}>Cancel</Button><Button kind="primary" onClick={() => void saveLocalModel()} disabled={modelBusy || !modelKey.trim()}>{modelBusy ? "Saving…" : "Save securely"}</Button></div></div></div>}
      </div>
      <div className="settings-card full-card credential-card"><div className="card-heading"><span className="card-icon">◇</span><div><h3>Tools &amp; credentials</h3><p>Choose a local tool and add only the credential it needs. Secrets stay encrypted on this Windows account and never upload.</p></div><span className="card-badge badge-private">Local only</span></div><div className="credential-status-grid"><button type="button" className={`credential-status ${hasCredential("tavily", "TAVILY_API_KEY") ? "status-configured" : ""} ${credentialProvider === "tavily" ? "status-selected" : ""}`} onClick={() => chooseCredentialProvider("tavily")} aria-pressed={credentialProvider === "tavily"}><span className="credential-status-icon">⌕</span><span><strong>Tavily Search</strong><small>{hasCredential("tavily", "TAVILY_API_KEY") ? "Configured on this PC" : "Not configured"}</small></span></button><button type="button" className={`credential-status ${hasCredential("github", "GITHUB_TOKEN") ? "status-configured" : ""} ${credentialProvider === "github" ? "status-selected" : ""}`} onClick={() => chooseCredentialProvider("github")} aria-pressed={credentialProvider === "github"}><span className="credential-status-icon">◈</span><span><strong>GitHub</strong><small>{hasCredential("github", "GITHUB_TOKEN") ? "Configured on this PC" : "Not configured"}</small></span></button><button type="button" className={`credential-status ${credentialProvider === "custom" ? "status-selected" : ""}`} onClick={() => chooseCredentialProvider("custom")} aria-pressed={credentialProvider === "custom"}><span className="credential-status-icon">＋</span><span><strong>Custom tool</strong><small>{credentials.filter((credential) => credential.provider === "custom").length ? `${credentials.filter((credential) => credential.provider === "custom").length} saved locally` : "Optional"}</small></span></button></div><div className="credential-selected"><span className="eyebrow">ADDING A CREDENTIAL FOR {selectedCredential.label.toUpperCase()}</span><span>{selectedCredential.description} Use the exact environment variable expected by this tool.</span></div><div className="credential-entry"><label>Tool<select value={credentialProvider} onChange={(event) => chooseCredentialProvider(event.target.value)}>{credentialPresets.map((preset) => <option value={preset.value} key={preset.value}>{preset.label}</option>)}</select></label><label>Environment variable<input aria-label="Environment variable name" value={credentialName} onChange={(event) => setCredentialName(event.target.value.toUpperCase())} placeholder="TOOL_API_KEY" spellCheck={false} /></label><label>Secret value<input aria-label={`Secret value for ${selectedCredential.label}`} type="password" value={credentialSecret} onChange={(event) => setCredentialSecret(event.target.value)} placeholder="Paste locally — never uploaded" autoComplete="off" /></label><Button kind="primary" onClick={() => void saveCredential()} disabled={!isNativeDesktop || credentialBusy || !credentialName.trim() || !credentialSecret}>{credentialBusy ? "Saving…" : "Save locally"}</Button></div><div className="credential-list">{credentials.length === 0 ? <span className="credential-empty">No local credentials saved. Hosted provider keys are managed separately.</span> : credentials.map((credential) => <div className="credential-row" key={credential.name}><span className="credential-provider">{credential.provider}</span><strong>{credential.name}</strong><span>••••••••</span><Button kind="quiet" onClick={() => void removeCredential(credential.name)} disabled={credentialBusy}>Remove</Button></div>)}</div><div className="permission-note"><span>▣</span><span>Smara injects a selected alias only into the approved process and redacts it from output. It never sends this value to the hosted service.</span></div></div>
      <div className="settings-card full-card connector-card"><div className="card-heading"><span className="card-icon">⌁</span><div><h3>Local connector access</h3><p>Each connector runs from this PC only. Safe reads can follow the Desktop policy; writes remain approval-gated.</p></div><span className="card-badge badge-private">Proof-only audit</span></div><div className="connector-list">{connectors.map((connector) => <div className={`connector-row ${connector.credential_configured ? "connector-ready" : ""}`} key={connector.provider}><div><strong>{connector.provider === "tavily" ? "Tavily Search" : "GitHub repositories"}</strong><span>{connector.credential_configured ? "Connected locally" : `Needs ${connector.credential_alias}`}</span><small>{connector.scopes.join(", ")} · {connector.risk.replaceAll("_", " ")} · max {connector.max_requests_per_run} request per approved run</small></div>{connector.credential_configured && <Button kind="quiet" onClick={() => void revokeConnector(connector.provider)} disabled={credentialBusy}>Disconnect</Button>}</div>)}</div>{connectors.some((connector) => connector.provider === "github" && connector.credential_configured) && !localIntegrationPaired && <div className="connector-warning" role="status"><strong>GitHub is saved, but this pairing cannot use local connectors yet.</strong><span>Generate a fresh desktop pairing code in Smara Web and include <b>local integration</b>, then pair this desktop again. Existing permissions stay unchanged.</span></div>}<div className="permission-note"><span>▣</span><span>Connection status and the local audit contain no keys, queries, response text, or repository names—only time, connector, operation, result count, and proof hash.</span></div></div>
       <div className="settings-card full-card permissions-card"><div className="card-heading"><span className="card-icon">⌂</span><div><h3>Local permissions</h3><p>These are the hard boundaries for files, terminal, and browser work. Empty means disabled.</p></div><span className="card-badge">Guardrails active</span></div><div className="permission-summary">{permissionSummary.map((item) => <div className={`permission-summary-item ${item.value ? "summary-enabled" : ""}`} key={item.label}><span className="permission-summary-icon">{item.icon}</span><div><strong>{item.label}</strong><span>{item.value ? `${item.value} ${item.detail}${item.value === 1 ? "" : "s"}` : "Disabled"}</span></div><span className="permission-state">{item.value ? "On" : "Off"}</span></div>)}</div><div className="approval-policy"><div><strong>Desktop approval policy</strong><span>{runtimeMode === "local" ? "This PC owns approval. Ask before each task, or choose Approve for me for declared local capabilities." : "This PC owns approval for local work. Hosted Smara can only report status."}</span></div><label className="policy-toggle"><input type="checkbox" checked={autoApproveSafe} onChange={(event) => setAutoApproveSafe(event.target.checked)} /><span className="toggle-track" aria-hidden="true"><span /></span><b>{autoApproveSafe ? "Safe reads auto-approved" : "Desktop approval required"}</b></label></div><div className="permission-grid"><label><span>Approved folders <em>{splitLines(roots).length} entries</em></span><textarea value={roots} onChange={(event) => setRoots(event.target.value)} placeholder={'C:\\Users\\you\\Documents'} /></label><label><span>Terminal executables <em>{splitLines(terminal).length} entries</em></span><textarea value={terminal} onChange={(event) => setTerminal(event.target.value)} placeholder={'python\ngit'} /></label><label><span>Browser domains <em>{splitLines(domains).length} entries</em></span><textarea value={domains} onChange={(event) => setDomains(event.target.value)} placeholder={'github.com\nexample.com'} /></label></div><div className="permission-note"><span>▣</span><span>Shell operators, path traversal, symlink escapes, unknown executables, and unapproved domains are rejected. Safe-read auto-approval is audited; it never grants write or terminal access.</span></div></div>
      <div className="settings-card full-card about-card"><div><span className="eyebrow">ABOUT THIS APP</span><h3>Local by default, cloud when useful</h3><p>Local mode keeps private chat, approvals, credentials, task state, and execution on this PC. Cloud mode adds hosted research, memory, schedules, and durable cross-device coordination without taking ownership of Desktop approvals.</p></div><div className="version">v0.1 beta<br /><span>Windows native</span></div></div>
    </div>
  </section>;
}

export default App;
