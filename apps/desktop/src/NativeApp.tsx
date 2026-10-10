import { useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import NativeSettings from "./NativeSettings";
import NativeMarkdown from "./NativeMarkdown";
import { nativeSettings, sameWorkspace, type NativeSetup } from "./nativeApi";
import { NativeRpc, RpcOutcomeUnknown, type RpcMessage } from "./nativeRpc";
import { activeTurn, answerKey, approvalResponse, displayItem, mergeItem, mergeWorkers, resumeStatus, routeMessage, supportsApproval, threadLabel, type NativeTurn, type ViewItem, type WorkerView } from "./nativeView";
import type { LocalModelProfile } from "./types";
import packageInfo from "../package.json";
import "./native-shell.css";

type Item = ViewItem;
type Approval = { id: number | string; method: string; params: Record<string, unknown> };
type Question = { id: string; question: string; isSecret?: boolean; options?: { label: string; description: string }[] | null };
type Thread = { id: string; name?: string | null; preview?: string };

export default function NativeApp() {
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsSaving, setSettingsSaving] = useState(false);
  const [setup, setSetup] = useState<NativeSetup | null>(null);
  const [addingProject, setAddingProject] = useState(false);
  const [projectPath, setProjectPath] = useState("");
  const [projectName, setProjectName] = useState("");
  const [workspace, setWorkspace] = useState("");
  const [profiles, setProfiles] = useState<LocalModelProfile[]>([]);
  const [profile, setProfile] = useState("");
  const [connected, setConnected] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const [loadingThread, setLoadingThread] = useState(false);
  const [built, setBuilt] = useState<boolean | null>(null);
  const [threads, setThreads] = useState<Thread[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [threadId, setThreadId] = useState("");
  const [turnId, setTurnId] = useState("");
  const [busy, setBusy] = useState(false);
  const [uncertain, setUncertain] = useState(false);
  const [toolsEnabled, setToolsEnabled] = useState(false);
  const [workersEnabled, setWorkersEnabled] = useState(false);
  const [workers, setWorkers] = useState<WorkerView[]>([]);
  const [browserOrigins, setBrowserOrigins] = useState("");
  const [draft, setDraft] = useState("");
  const [items, setItems] = useState<Item[]>([]);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  const [status, setStatus] = useState("Preparing project…");
  const threadRef = useRef("");
  const clientRef = useRef<NativeRpc | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const submitted = useRef("");
  const projectDrafts = useRef<Record<string, string>>({});
  const previousTurn = useRef("");
  const activeTurnRef = useRef("");
  const busyRef = useRef(false);
  const lifecycleRef = useRef(false);
  const loadingThreadRef = useRef(false);
  const restoreThreadRef = useRef("");
  const settingsPendingRef = useRef(false);

  const updateItem = (id: string, kind: string, text: string, append = false, itemStatus?: string) => {
    setItems(current => mergeItem(current, { id, kind, text, status: itemStatus }, append));
  };
  const accepted = () => {
    const text = submitted.current;
    if (text) {
      setDraft(current => current === text ? "" : current);
      setThreads(current => current.map(thread => thread.id === threadRef.current && !thread.preview ? { ...thread, preview: text } : thread));
    }
    submitted.current = ""; setUncertain(false);
  };

  const receive = (message: RpcMessage) => {
    const params = message.params ?? {};
    const route = routeMessage(message, threadRef.current);
    if (route === "resolved") {
      setApprovals(current => current.filter(item => item.id !== params.requestId));
      return;
    }
    if (route === "request") {
      setSettingsOpen(false);
      setApprovals(current => [...current.filter(item => item.id !== message.id), { id: message.id!, method: message.method!, params }]);
      setStatus("Waiting for your response");
      return;
    }
    if (route === "ignore") return;
    if (message.method === "item/agentMessage/delta") {
      updateItem(String(params.itemId), "assistant", String(params.delta ?? ""), true);
    } else if (message.method === "item/commandExecution/outputDelta") {
      updateItem(String(params.itemId), "commandExecution", String(params.delta ?? ""), true);
    } else if (message.method === "item/started" || message.method === "item/completed") {
      const item = params.item as Record<string, unknown> | undefined;
      if (!item) return;
      setWorkers(current => mergeWorkers(current, item));
      setItems(current => mergeItem(current, displayItem(item), false, message.method === "item/started"));
    } else if (message.method === "turn/started") {
      const turn = params.turn as { id: string };
      accepted(); activeTurnRef.current = turn.id; busyRef.current = true;
      setTurnId(turn.id); setBusy(true); setStatus("Working");
    } else if (message.method === "turn/completed") {
      const turn = params.turn as { id: string; status: string; error?: { message?: string } };
      if (activeTurnRef.current && turn.id !== activeTurnRef.current) return;
      if (!activeTurnRef.current && submitted.current && turn.id === previousTurn.current) return;
      previousTurn.current = turn.id; activeTurnRef.current = ""; busyRef.current = false;
      accepted(); setBusy(false); setTurnId("");
      // A parent completing must not discard a worker's outstanding request.
      // Native serverRequest/resolved is the authority for request lifetime.
      setApprovals(current => current.filter(item => !(item.params.threadId === params.threadId && item.params.turnId === turn.id)));
      setStatus(turn.status);
      if (turn.status !== "completed" && turn.error?.message) setError(turn.error.message);
    } else if (message.method === "error" || message.method === "smara/runtimeError") {
      setError(String(params.message ?? (params.error as { message?: string })?.message ?? "Native runtime error"));
    } else if (message.method === "smara/runtimeStopped") {
      activeTurnRef.current = ""; busyRef.current = false;
      setConnected(false); setBusy(false); setUncertain(false); setApprovals([]); setStatus("Runtime stopped; unfinished work is not reported as complete");
    }
  };

  useEffect(() => {
    let disposed = false;
    void Promise.all([nativeSettings(), invoke<{ built: boolean }>("native_status")])
      .then(([initial, native]) => {
        if (disposed) return;
        setSetup(initial); setWorkspace(initial.preferences.workspace); setProfiles(initial.profiles); setBuilt(native.built);
        const selected = initial.preferences.active_model;
        setProfile(initial.profiles.some(model => model.id === selected) ? selected : "");
      }).catch(reason => { if (!disposed) setError(String(reason)); });
    return () => { disposed = true; void clientRef.current?.stop().catch(() => { /* App exit also owns native cleanup. */ }); };
  }, []);
  useEffect(() => {
    const pane = bottom.current?.parentElement;
    if (pane && pane.scrollHeight - pane.scrollTop - pane.clientHeight < 250) bottom.current?.scrollIntoView({ block: "end" });
  }, [items, busy, approvals.length]);

  const connect = async () => {
    if (lifecycleRef.current || busyRef.current || loadingThreadRef.current) return null;
    if (!workspace || !profile || built !== true) return null;
    const restoreId = restoreThreadRef.current || threadRef.current;
    lifecycleRef.current = true;
    setError(""); setConnecting(true); setStatus("Preparing project…");
    let client: NativeRpc | null = null;
    try {
      // Dispose the crashed client's listener and generation before reconnect.
      // A new process must load a saved conversation through thread/resume;
      // it cannot reuse a thread ID that was only loaded in the old process.
      await clientRef.current?.stop();
      threadRef.current = ""; setThreadId(""); setTurnId("");
      activeTurnRef.current = ""; busyRef.current = false;
      setItems([]); setWorkers([]); setApprovals([]); setAnswers({}); setUncertain(false); setBusy(false);
      client = new NativeRpc(receive);
      clientRef.current = client;
      await client.connect(workspace, profile, toolsEnabled, workersEnabled, browserOrigins.split(/\s+/).filter(Boolean));
      setConnected(true); setStatus("Ready");
      const list = await client.request<{ data: Thread[]; nextCursor?: string | null }>("thread/list", { limit: 50, cwd: workspace });
      setThreads(list.data); setNextCursor(list.nextCursor ?? null);
      if (restoreId) {
        restoreThreadRef.current = restoreId;
        if (!await loadThread(client, restoreId)) return null;
      }
      return client;
    } catch (reason) {
      setConnected(false); setError(String(reason));
      try { await client?.stop(); }
      catch (cleanup) { setError(`${String(reason)}. Native cleanup is unconfirmed: ${String(cleanup)}. Reconnect to retry cleanup.`); }
      setStatus("Project setup needs attention");
      return null;
    }
    finally { lifecycleRef.current = false; setConnecting(false); }
  };
  const loadThread = async (client: NativeRpc, id: string) => {
    const generation = client.generation;
    loadingThreadRef.current = true; setLoadingThread(true); setError(""); setStatus("Loading saved conversation…");
    try {
      const saved = await client.request<{ thread: { cwd: string } }>("thread/read", { threadId: id });
      if (!sameWorkspace(saved.thread.cwd, workspace)) throw new Error("This conversation belongs to another project. Open that project to resume it.");
      const response = await client.request<{ thread: { id: string; turns: NativeTurn[] } }>("thread/resume", { threadId: id, model: profiles.find(model => model.id === profile)?.model, modelProvider: "smara_chat_adapter", approvalPolicy: "on-request", sandbox: "workspace-write" });
      if (client !== clientRef.current || client.generation !== generation) return false;
      threadRef.current = id; setThreadId(id); setItems([]);
      restoreThreadRef.current = "";
      setItems((response.thread.turns ?? []).flatMap(turn => turn.items.map(displayItem)));
      setWorkers((response.thread.turns ?? []).flatMap(turn => turn.items).reduce(mergeWorkers, [] as WorkerView[]));
      previousTurn.current = response.thread.turns.at(-1)?.id ?? "";
      const active = activeTurn(response.thread.turns ?? []);
      activeTurnRef.current = active?.id ?? ""; busyRef.current = !!active;
      setBusy(!!active); setTurnId(active?.id ?? "");
      setStatus(resumeStatus(response.thread.turns ?? []));
      return true;
    } catch (reason) { if (client === clientRef.current && client.generation === generation) setError(String(reason)); return false; }
    finally { loadingThreadRef.current = false; setLoadingThread(false); }
  };
  const selectThread = async (id: string) => {
    if (busyRef.current || lifecycleRef.current || loadingThreadRef.current || !connected) return;
    await loadThread(clientRef.current!, id);
  };
  const send = async () => {
    if (!draft.trim() || busyRef.current || lifecycleRef.current || loadingThreadRef.current || approvals.length || !profile || built !== true) return;
    const textToSend = draft;
    if (!connected && !await connect()) return;
    // A failed history restoration must never turn a reply into a new chat.
    if (restoreThreadRef.current || busyRef.current) return;
    busyRef.current = true;
    setError(""); setBusy(true); submitted.current = textToSend;
    try {
      let id = threadRef.current;
      if (!id) {
        const result = await clientRef.current!.request<{ thread: Thread }>("thread/start", { cwd: workspace, approvalPolicy: "on-request", sandbox: "workspace-write", developerInstructions: "You are Smara. Be honest about tool outcomes. Do not claim tests passed without evidence. Ask before external writes, payments, publishing, or messaging people." });
        id = result.thread.id; threadRef.current = id; setThreadId(id);
        setThreads(current => [result.thread, ...current]);
      }
      const text = submitted.current;
      // Keep the draft until native confirms acceptance. A timeout is not
      // permission to repeat a potentially accepted turn automatically.
      await clientRef.current!.request("turn/start", { threadId: id, input: [{ type: "text", text, text_elements: [] }] });
      accepted();
    } catch (reason) {
      setError(String(reason));
      if (reason instanceof RpcOutcomeUnknown) {
        if (submitted.current) { setUncertain(true); setStatus("Acceptance unknown — check status or recover chat before retrying. No automatic resend."); }
      } else { busyRef.current = false; setBusy(false); submitted.current = ""; }
    }
  };
  const checkStatus = async () => {
    if (!threadRef.current) { setError("No confirmed conversation ID. Recover this chat; do not resend automatically."); return; }
    try {
      const result = await clientRef.current!.request<{ thread: { turns: NativeTurn[] } }>("thread/read", { threadId: threadRef.current, includeTurns: true });
      const turns = result.thread.turns ?? [];
      const current = activeTurn(turns);
      const latest = turns.at(-1);
      if (!current && (!latest || latest.id === previousTurn.current)) {
        setStatus("No new turn confirmed. Send stays locked; use Recover chat."); return;
      }
      accepted(); previousTurn.current = latest?.id ?? previousTurn.current;
      setItems(turns.flatMap(turn => turn.items.map(displayItem)));
      setWorkers(turns.flatMap(turn => turn.items).reduce(mergeWorkers, [] as WorkerView[]));
      setBusy(!!current); setTurnId(current?.id ?? "");
      busyRef.current = !!current; activeTurnRef.current = current?.id ?? "";
      setStatus(current ? "Working — native status confirmed" : latest?.status ?? "Ready");
    } catch (reason) { setError(String(reason)); }
  };
  const inspectWorker = async (id: string) => {
    const client = clientRef.current;
    if (!client || !connected || lifecycleRef.current) return;
    const generation = client.generation;
    const parent = threadRef.current;
    try {
      // Read only: never resume/relocate a worker into the parent's workspace.
      const result = await client.request<{ thread: { cwd: string; turns: NativeTurn[] } }>("thread/read", { threadId: id, includeTurns: true });
      if (client !== clientRef.current || client.generation !== generation || parent !== threadRef.current) return;
      const latest = result.thread.turns.at(-1);
      setWorkers(current => current.map(worker => worker.id === id ? { ...worker, cwd: result.thread.cwd, status: latest?.status ?? "no turn recorded" } : worker));
    } catch (reason) { setError(String(reason)); }
  };
  const decide = async (approval: Approval, allow: boolean) => {
    try {
      await clientRef.current!.respond(approval.id, approvalResponse(approval.method, approval.params, allow));
      setApprovals(current => current.filter(item => item.id !== approval.id)); setStatus("Working");
    } catch (reason) { setError(String(reason)); }
  };
  const stopTurn = async () => {
    try { await clientRef.current!.request("turn/interrupt", { threadId, turnId }); }
    catch (reason) { setError(String(reason)); }
  };
  const answerQuestions = async (request: Approval) => {
    try {
      const questions = request.params.questions as Question[];
      if (questions.some(question => !answers[answerKey(request.id, question.id)]?.trim())) throw new Error("Answer each question before continuing");
      await clientRef.current!.respond(request.id, { answers: Object.fromEntries(questions.map(question => [question.id, { answers: [answers[answerKey(request.id, question.id)]] }])) });
      setApprovals(current => current.filter(item => item.id !== request.id));
      setAnswers(current => Object.fromEntries(Object.entries(current).filter(([key]) => !questions.some(question => key === answerKey(request.id, question.id)))));
      setStatus("Working");
    } catch (reason) { setError(String(reason)); }
  };
  const disconnect = async (preserveConversation = false) => {
    if (lifecycleRef.current) return false;
    restoreThreadRef.current = preserveConversation ? threadRef.current || restoreThreadRef.current : "";
    lifecycleRef.current = true; setConnecting(true); setStatus("Updating project…");
    try {
      await clientRef.current?.stop(); setStatus("Ready");
      return true;
    } catch (reason) {
      setError(String(reason)); setStatus("Disconnect outcome unknown; reconnect will recheck the native process");
      return false;
    }
    finally {
      setConnected(false); setBusy(false); setUncertain(false);
      busyRef.current = false; activeTurnRef.current = "";
      setApprovals([]); setAnswers({}); setTurnId(""); threadRef.current = "";
      if (!preserveConversation) { setThreadId(""); setItems([]); setWorkers([]); }
      lifecycleRef.current = false; setConnecting(false);
    }
  };

  const updateSetup = (value: NativeSetup) => {
    setSetup(value); setProfiles(value.profiles);
    if (!value.profiles.some(model => model.id === profile)) setProfile("");
  };
  const newConversation = () => {
    if (busyRef.current || lifecycleRef.current || loadingThreadRef.current || settingsPendingRef.current || approvals.length) return;
    setThreadId(""); threadRef.current = ""; restoreThreadRef.current = ""; setItems([]); setWorkers([]); setError(""); setSettingsOpen(false);
  };
  const switchProject = async (path: string, add = false) => {
    if (busyRef.current || lifecycleRef.current || loadingThreadRef.current || settingsPendingRef.current || approvals.length || workers.some(worker => ["running", "pendingInit", "inProgress"].includes(worker.status))) return;
    if (connected && !await disconnect()) return;
    lifecycleRef.current = true; setConnecting(true); setError("");
    try {
      projectDrafts.current[workspace] = draft;
      const next = await nativeSettings(add ? "add_project" : "select_project", { workspace: path, label: projectName });
      updateSetup(next); setWorkspace(next.preferences.workspace); setThreads([]); setNextCursor(null); setItems([]); setWorkers([]);
      restoreThreadRef.current = ""; threadRef.current = ""; setThreadId("");
      setToolsEnabled(false); setWorkersEnabled(false); setBrowserOrigins(""); setDraft(projectDrafts.current[next.preferences.workspace] ?? "");
      setAddingProject(false); setProjectPath(""); setProjectName(""); setSettingsOpen(false);
      setStatus("Project selected");
    } catch (reason) { setError(String(reason)); }
    finally { lifecycleRef.current = false; setConnecting(false); }
  };
  const selectModel = async (id: string) => {
    if (busyRef.current || lifecycleRef.current || loadingThreadRef.current || settingsPendingRef.current || approvals.length) return;
    if (id === profile) return;
    if (connected && !await disconnect(true)) return;
    lifecycleRef.current = true; setConnecting(true); setError("");
    try { updateSetup(await nativeSettings("select_model", { id })); setProfile(id); }
    catch (reason) { setError(String(reason)); }
    finally { lifecycleRef.current = false; setConnecting(false); }
  };
  const project = setup?.preferences.projects.find(p => sameWorkspace(p.workspace, workspace));
  const navigationLocked = busy || connecting || loadingThread || settingsSaving || approvals.length > 0 || workers.some(worker => ["running", "pendingInit", "inProgress"].includes(worker.status));
  const toggleSettings = async () => {
    if (navigationLocked || lifecycleRef.current || settingsPendingRef.current) return;
    if (settingsOpen) { setSettingsOpen(false); return; }
    // Pause the idle transport internally so credentials can be edited safely.
    // The selected conversation is restored, never resent, on return to chat.
    if (connected && !await disconnect(true)) return;
    setSettingsOpen(true);
  };
  const recoverSession = async () => {
    if (lifecycleRef.current || loadingThreadRef.current) return;
    if (await disconnect(true)) await connect();
  };
  const loadOlder = async () => {
    const client = clientRef.current;
    if (!client || !connected || !nextCursor || loadingMore || navigationLocked) return;
    const generation = client.generation;
    setLoadingMore(true);
    try {
      const list = await client.request<{ data: Thread[]; nextCursor?: string | null }>("thread/list", { limit: 50, cwd: workspace, cursor: nextCursor });
      if (client !== clientRef.current || client.generation !== generation) return;
      setThreads(current => [...current, ...list.data.filter(thread => !current.some(t => t.id === thread.id))]);
      setNextCursor(list.nextCursor ?? null);
    } catch (reason) { setError(String(reason)); }
    finally { setLoadingMore(false); }
  };
  useEffect(() => {
    const shortcut = (event: KeyboardEvent) => {
      if ((!event.ctrlKey && !event.metaKey) || event.altKey || navigationLocked) return;
      if (event.key.toLowerCase() === "n") { event.preventDefault(); newConversation(); }
      if (event.key === ",") { event.preventDefault(); void toggleSettings(); }
    };
    window.addEventListener("keydown", shortcut);
    return () => window.removeEventListener("keydown", shortcut);
  }, [navigationLocked, settingsOpen, connected]);
  useEffect(() => {
    if (built === true && workspace && profile && !settingsOpen) void connect();
    // Context changes prepare a new transport, not a turn. Crashes/timeouts do
    // not trigger a retry loop or automatically repeat user work.
  }, [built, workspace, profile, settingsOpen]);
  return <div className="native-app">
    <div className="native-layout"><aside>
      <div className="native-brand"><span className="brand-mark">s</span><strong>Smara</strong></div>
      <button className="new-chat" disabled={navigationLocked} onClick={newConversation}>＋ New chat</button>
      <div className="sidebar-section-heading"><span>Projects</span><button aria-label="Add project" disabled={navigationLocked} onClick={() => setAddingProject(!addingProject)}>＋</button></div>
      {setup?.preferences.projects.map(p => <button className={`project-button ${sameWorkspace(p.workspace, workspace) ? "selected" : ""}`} key={p.workspace} title={p.workspace} disabled={navigationLocked} onClick={() => { if (!sameWorkspace(p.workspace, workspace)) void switchProject(p.workspace); else setSettingsOpen(false); }}><span>▱</span>{p.label}</button>)}
      {(!setup?.preferences.projects.length || addingProject) && <form className="project-form" onSubmit={event => { event.preventDefault(); void switchProject(projectPath, true); }}><label>Project name<input aria-label="Project name" value={projectName} disabled={navigationLocked} placeholder="Optional name" onChange={e => setProjectName(e.target.value)} /></label><label>Local folder<input aria-label="Project folder" required value={projectPath} disabled={navigationLocked} placeholder="C:\\path\\to\\your-project" onChange={e => setProjectPath(e.target.value)} /></label><small>Adding a folder makes it available as a project. Native sandboxing still applies.</small><button disabled={navigationLocked || !projectPath.trim()}>Add project</button></form>}
      <div className="sidebar-section-heading"><span>Chats</span><span>{threads.length || ""}</span></div>
      <div className="sidebar-threads">{threads.map(thread => <button className={thread.id === threadId ? "selected" : ""} key={thread.id} title={thread.name || thread.preview || "New conversation"} disabled={navigationLocked || !connected} onClick={() => { setSettingsOpen(false); void selectThread(thread.id); }}>{threadLabel(thread)}</button>)}
      {!threads.length && <p>{connecting ? "Loading chats…" : "Your chats appear here."}</p>}{nextCursor && <button disabled={navigationLocked || loadingMore || !connected} onClick={() => void loadOlder()}>{loadingMore ? "Loading…" : "Show more"}</button>}</div>
      <div className="sidebar-bottom"><button disabled={navigationLocked} className={settingsOpen ? "selected" : ""} onClick={() => void toggleSettings()}>⚙ Settings</button></div>
    </aside><main>
      <header className="native-topbar"><span className="project-icon" aria-hidden="true">▱</span><strong title={workspace}>{settingsOpen ? "Settings" : project?.label || "Choose a project"}</strong>{!settingsOpen && threadId && <span className="header-chat-title">{threadLabel(threads.find(thread => thread.id === threadId) ?? { id: threadId })}</span>}</header>
      {built === false && <div className="native-warning">Native runtime is missing. Build scripts/build-smara-native.ps1; no old-engine fallback is used.</div>}
      {error && <div role="alert" className="native-error">{error}{!settingsOpen && (!busy || uncertain) && <button disabled={connecting || loadingThread} onClick={() => void recoverSession()}>Retry setup</button>}<button aria-label="Dismiss error" onClick={() => setError("")}>×</button></div>}
      {settingsOpen && setup ? <NativeSettings setup={setup} locked={connected || connecting} onUpdate={updateSetup} onClose={() => { if (!settingsPendingRef.current) setSettingsOpen(false); }} onPendingChange={pending => { settingsPendingRef.current = pending; setSettingsSaving(pending); }} version={packageInfo.version} toolsEnabled={toolsEnabled} workersEnabled={workersEnabled} browserOrigins={browserOrigins} onToolsChange={setToolsEnabled} onWorkersChange={setWorkersEnabled} onOriginsChange={setBrowserOrigins} /> : <>
      {uncertain && <div className="native-warning"><button onClick={() => void checkStatus()}>Check response status</button><button disabled={connecting || loadingThread} onClick={() => void recoverSession()}>Recover chat</button> The request may have been accepted. Your message will not be resent automatically.</div>}
      <div className="native-transcript" aria-live="polite">
        {!!workers.length && <section className="native-workers"><strong>Native workers · changes are not auto-merged</strong>{workers.map(worker => <div key={worker.id}><code>{worker.id}</code> · {worker.status} <button disabled={!connected || connecting || loadingThread} onClick={() => void inspectWorker(worker.id)}>Check status & checkout</button>{worker.cwd && <pre>{worker.cwd}</pre>}</div>)}</section>}
        {!items.length && <div className="native-empty"><h1>What can I help you with?</h1><p>Research, build, and get work done.</p><div className="starter-cards">{[{ title: "Understand this project", text: "Inspect this project and explain its architecture, entry points and how to run its tests. Do not change files." }, { title: "Find & fix a bug", text: "Inspect this project for a reproducible bug. Show evidence, make a minimal fix, and run the relevant tests." }, { title: "Research a topic", text: "Research a topic using public sources. Read the actual clock for time-sensitive questions, verify claims against fetched pages and cite them. Ask me for the topic first." }].map(card => <button key={card.title} title={card.text} onClick={() => setDraft(card.text)}>{card.title}<span>↗</span></button>)}</div>{!profile && <small className="empty-hint">Add your model in Settings to get started.</small>}</div>}
        {items.map(item => <article key={item.id} className={`native-item ${item.kind}`}><div className="item-heading"><small>{item.kind === "assistant" ? "Smara" : item.kind === "user" ? "You" : item.kind} {item.status && `· ${item.status}`}</small><button className="text-button" onClick={() => void navigator.clipboard.writeText(item.text).catch(() => setError("Clipboard is unavailable"))}>Copy</button></div>{item.kind === "assistant" ? <NativeMarkdown text={item.text} /> : item.kind === "user" ? <pre>{item.text}</pre> : <details open={item.status === "inProgress" || item.status === "failed"}><summary>Tool details · {item.text.length.toLocaleString()} characters</summary><pre>{item.text}</pre></details>}</article>)}
        {approvals.map(approval => <section className="native-approval" key={approval.id}>
          {approval.params.threadId !== undefined && approval.params.threadId !== threadId && <p>Worker / other native conversation: {String(approval.params.threadId)}. This request is not auto-approved.</p>}
          {approval.method === "item/tool/requestUserInput" ? <>
            <strong>Smara needs your input</strong>
            {(approval.params.questions as Question[]).map(question => <label key={question.id} style={{ display: "block", marginTop: 12 }}>{question.question}
              {question.options?.map(option => <button type="button" key={option.label} title={option.description} onClick={() => setAnswers(current => ({ ...current, [answerKey(approval.id, question.id)]: option.label }))}>{option.label}</button>)}
              <input type={question.isSecret ? "password" : "text"} value={answers[answerKey(approval.id, question.id)] ?? ""} onChange={event => setAnswers(current => ({ ...current, [answerKey(approval.id, question.id)]: event.target.value }))} />
            </label>)}<button onClick={() => void answerQuestions(approval)}>Continue</button>
          </> : <><strong>Permission required</strong><p>{approval.method}</p><pre>{JSON.stringify(approval.params, null, 2)}</pre>
            {supportsApproval(approval.method, approval.params)
              ? <><button onClick={() => void decide(approval, true)}>Allow this action</button><button onClick={() => void decide(approval, false)}>Deny</button></>
              : <><p>This specialized request cannot be approved here; no automatic grant.</p>{approval.method === "mcpServer/elicitation/request" && <button onClick={() => void decide(approval, false)}>Deny request</button>}</>}
          </>}
        </section>)}
        <div ref={bottom} />
      </div>
      <footer><form className="native-composer" onSubmit={event => { event.preventDefault(); void send(); }}><textarea aria-label="Message Smara" rows={2} placeholder="Ask Smara anything…" value={draft} onChange={event => { setDraft(event.target.value); event.target.style.height = "auto"; event.target.style.height = `${Math.min(event.target.scrollHeight, 260)}px`; }} onKeyDown={event => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void send(); } }} /><div className="composer-toolbar"><small>{connecting ? "Preparing project…" : ""}</small><div className="composer-actions"><select aria-label="Model" title="Choose a model" value={profile} disabled={navigationLocked} onChange={event => void selectModel(event.target.value)}><option value="" disabled>Choose model</option>{profiles.map(model => <option value={model.id} key={model.id}>{model.label}</option>)}</select><button className={busy ? "stop-button" : "primary"} aria-label={busy ? "Stop response" : "Send message"} title={busy ? "Stop response" : "Send message"} type={busy ? "button" : "submit"} disabled={connecting || loadingThread || (busy ? !turnId : !profile || built !== true || !draft.trim() || approvals.length > 0)} onClick={busy ? () => void stopTurn() : undefined}>{busy ? "■" : "↑"}</button></div></div></form><div className="composer-status"><small role="status">{busy || uncertain || loadingThread || (status !== "Ready" && status !== "completed") ? status : ""}</small><small>Shift+Enter for a new line</small></div></footer>
      </>}
    </main></div>
  </div>;
}
