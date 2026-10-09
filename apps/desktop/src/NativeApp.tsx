import { useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import LegacyApp from "./App";
import { desktop } from "./api";
import { NativeRpc, RpcOutcomeUnknown, type RpcMessage } from "./nativeRpc";
import { activeTurn, answerKey, approvalResponse, displayItem, mergeItem, resumeStatus, routeMessage, supportsApproval, threadLabel, type NativeTurn, type ViewItem } from "./nativeView";
import type { LocalModelProfile } from "./types";
import packageInfo from "../package.json";
import "./native.css";

type Item = ViewItem;
type Approval = { id: number | string; method: string; params: Record<string, unknown> };
type Question = { id: string; question: string; isSecret?: boolean; options?: { label: string; description: string }[] | null };
type Thread = { id: string; name?: string | null; preview?: string };

export default function NativeApp() {
  const [legacy, setLegacy] = useState(false);
  const [workspace, setWorkspace] = useState("");
  const [profiles, setProfiles] = useState<LocalModelProfile[]>([]);
  const [profile, setProfile] = useState("");
  const [connected, setConnected] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const [loadingThread, setLoadingThread] = useState(false);
  const [built, setBuilt] = useState<boolean | null>(null);
  const [threads, setThreads] = useState<Thread[]>([]);
  const [threadId, setThreadId] = useState("");
  const [turnId, setTurnId] = useState("");
  const [busy, setBusy] = useState(false);
  const [uncertain, setUncertain] = useState(false);
  const [toolsEnabled, setToolsEnabled] = useState(false);
  const [draft, setDraft] = useState("");
  const [items, setItems] = useState<Item[]>([]);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  const [status, setStatus] = useState("Disconnected");
  const threadRef = useRef("");
  const clientRef = useRef<NativeRpc | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const submitted = useRef("");
  const previousTurn = useRef("");
  const activeTurnRef = useRef("");
  const busyRef = useRef(false);
  const lifecycleRef = useRef(false);
  const loadingThreadRef = useRef(false);

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
    void Promise.all([desktop.connection(), desktop.modelProfiles(), invoke<{ built: boolean }>("native_status")])
      .then(([connection, models, native]) => {
        if (disposed) return;
        setWorkspace(connection.workspace); setProfiles(models); setBuilt(native.built);
        const selected = connection.model_profile.replace(/^local:/, "");
        setProfile(models.some(model => model.id === selected) ? selected : "");
      }).catch(reason => { if (!disposed) setError(String(reason)); });
    return () => { disposed = true; void clientRef.current?.stop().catch(() => { /* App exit also owns native cleanup. */ }); };
  }, [legacy]);
  useEffect(() => {
    const pane = bottom.current?.parentElement;
    if (pane && pane.scrollHeight - pane.scrollTop - pane.clientHeight < 250) bottom.current?.scrollIntoView({ block: "end" });
  }, [items, busy, approvals.length]);

  const connect = async () => {
    if (lifecycleRef.current) return;
    lifecycleRef.current = true;
    setError(""); setConnecting(true);
    let client: NativeRpc | null = null;
    try {
      // Dispose the crashed client's listener and generation before reconnect.
      // A new process must load a saved conversation through thread/resume;
      // it cannot reuse a thread ID that was only loaded in the old process.
      await clientRef.current?.stop();
      threadRef.current = ""; setThreadId(""); setTurnId("");
      activeTurnRef.current = ""; busyRef.current = false;
      setItems([]); setApprovals([]); setAnswers({}); setUncertain(false); setBusy(false);
      client = new NativeRpc(receive);
      clientRef.current = client;
      await client.connect(workspace, profile, toolsEnabled);
      setConnected(true); setStatus("Ready — workspace sandbox, approval on request");
      const list = await client.request<{ data: Thread[] }>("thread/list", { limit: 50, cwd: workspace });
      setThreads(list.data);
    } catch (reason) {
      setConnected(false); setError(String(reason));
      try { await client?.stop(); }
      catch (cleanup) { setError(`${String(reason)}. Native cleanup is unconfirmed: ${String(cleanup)}. Reconnect to retry cleanup.`); }
    }
    finally { lifecycleRef.current = false; setConnecting(false); }
  };
  const selectThread = async (id: string) => {
    if (busyRef.current || lifecycleRef.current || loadingThreadRef.current || !connected) return;
    const client = clientRef.current!;
    const generation = client.generation;
    loadingThreadRef.current = true; setLoadingThread(true); setError(""); setStatus("Loading saved conversation…");
    try {
      const response = await client.request<{ thread: { id: string; turns: NativeTurn[] } }>("thread/resume", { threadId: id, cwd: workspace, approvalPolicy: "on-request", sandbox: "workspace-write" });
      if (client !== clientRef.current || client.generation !== generation || lifecycleRef.current) return;
      threadRef.current = id; setThreadId(id); setItems([]);
      setItems((response.thread.turns ?? []).flatMap(turn => turn.items.map(displayItem)));
      previousTurn.current = response.thread.turns.at(-1)?.id ?? "";
      const active = activeTurn(response.thread.turns ?? []);
      activeTurnRef.current = active?.id ?? ""; busyRef.current = !!active;
      setBusy(!!active); setTurnId(active?.id ?? "");
      setStatus(resumeStatus(response.thread.turns ?? []));
    } catch (reason) { if (client === clientRef.current && client.generation === generation) setError(String(reason)); }
    finally { loadingThreadRef.current = false; setLoadingThread(false); }
  };
  const send = async () => {
    if (!draft.trim() || busyRef.current || lifecycleRef.current || loadingThreadRef.current || !connected) return;
    busyRef.current = true;
    setError(""); setBusy(true); submitted.current = draft;
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
        if (submitted.current) { setUncertain(true); setStatus("Acceptance unknown — check status or disconnect before retrying. No automatic resend."); }
      } else { busyRef.current = false; setBusy(false); submitted.current = ""; }
    }
  };
  const checkStatus = async () => {
    if (!threadRef.current) { setError("No confirmed conversation ID. Disconnect and reconnect; do not resend automatically."); return; }
    try {
      const result = await clientRef.current!.request<{ thread: { turns: NativeTurn[] } }>("thread/read", { threadId: threadRef.current, includeTurns: true });
      const turns = result.thread.turns ?? [];
      const current = activeTurn(turns);
      const latest = turns.at(-1);
      if (!current && (!latest || latest.id === previousTurn.current)) {
        setStatus("No new turn confirmed. Send stays locked; disconnect and resume to recover."); return;
      }
      accepted(); previousTurn.current = latest?.id ?? previousTurn.current;
      setItems(turns.flatMap(turn => turn.items.map(displayItem)));
      setBusy(!!current); setTurnId(current?.id ?? "");
      busyRef.current = !!current; activeTurnRef.current = current?.id ?? "";
      setStatus(current ? "Working — native status confirmed" : latest?.status ?? "Ready");
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
  const disconnect = async () => {
    if (lifecycleRef.current) return false;
    lifecycleRef.current = true; setConnecting(true); setStatus("Disconnecting — waiting for native cleanup…");
    try {
      await clientRef.current?.stop(); setStatus("Disconnected; saved conversations are preserved");
      return true;
    } catch (reason) {
      setError(String(reason)); setStatus("Disconnect outcome unknown; reconnect will recheck the native process");
      return false;
    }
    finally {
      setConnected(false); setBusy(false); setUncertain(false);
      busyRef.current = false; activeTurnRef.current = "";
      setApprovals([]); setAnswers({}); setTurnId(""); setThreadId(""); threadRef.current = ""; setItems([]);
      lifecycleRef.current = false; setConnecting(false);
    }
  };

  if (legacy) return <><div className="native-legacy-banner"><button onClick={() => setLegacy(false)}>← Native Smara</button>Legacy settings and tools — old execution engine retained for migration, not native acceptance.</div><LegacyApp /></>;
  return <div className="native-app">
    <header><strong>🟢 Smara v{packageInfo.version}</strong><span>Source-native runtime · migration candidate</span><button disabled={connecting} onClick={() => { void disconnect().then(stopped => { if (stopped) setLegacy(true); }); }}>Settings & legacy tools</button></header>
    <div className="native-layout"><aside>
      <button disabled={busy || connecting || loadingThread} onClick={() => { setThreadId(""); threadRef.current = ""; setItems([]); setError(""); }}>＋ New conversation</button>
      <h3>Native conversations</h3>
      {threads.map(thread => <button className={thread.id === threadId ? "selected" : ""} key={thread.id} title={thread.name || thread.preview || "New conversation"} disabled={busy || connecting || loadingThread || !connected} onClick={() => void selectThread(thread.id)}>{threadLabel(thread)}</button>)}
      <p>Existing Python conversations remain untouched in the legacy view. They are not native sessions.</p>
    </aside><main>
      <section className="native-connection"><label>Workspace<input value={workspace} disabled={connected || connecting} onChange={event => setWorkspace(event.target.value)} /></label>
        <label>Model<select value={profile} disabled={connected || connecting} onChange={event => setProfile(event.target.value)}><option value="" disabled>Select a configured model</option>{profiles.map(model => <option value={model.id} key={model.id}>{model.label}</option>)}</select></label>
        <button disabled={connecting || (!connected && (!workspace || !profile || built === false))} onClick={() => void (connected ? disconnect() : connect())}>{connecting ? connected ? "Disconnecting…" : "Connecting…" : connected ? "Disconnect" : "Connect"}</button>
      </section>
      {built === false && <div className="native-warning">The full source has been imported, but the native executable is not built/bundled. Run scripts/build-smara-native.ps1. No silent fallback to the previous engine.</div>}
      {error && <div role="alert" className="native-error">{error}</div>}
      <label className="native-tools-choice"><input type="checkbox" checked={toolsEnabled} disabled={connected || connecting} onChange={event => setToolsEnabled(event.target.checked)} /> Research & local-memory readers · searches send queries to your search provider; no personal-browser or computer actions</label>
      {uncertain && <div className="native-warning"><button onClick={() => void checkStatus()}>Check native turn status</button> Send stays locked until acceptance is known. Disconnect is available.</div>}
      <div className="native-transcript" aria-live="polite">
        {!items.length && <div className="native-empty"><h1>One runtime. Your workspace.</h1><p>Choose a folder and model, connect, then ask Smara to inspect, code or run tests.</p><p>Public research and workspace-memory readers are opt-in. Interactive browser/computer use and remote memory are not yet native. Native tools and approvals use the imported harness source.</p></div>}
        {items.map(item => <article key={item.id} className={`native-item ${item.kind}`}><small>{item.kind} {item.status && `· ${item.status}`}</small><pre>{item.text}</pre></article>)}
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
      <footer><form onSubmit={event => { event.preventDefault(); void send(); }}><textarea aria-label="Message Smara" rows={3} placeholder="Ask Smara… Enter to send, Shift+Enter for a newline" value={draft} onChange={event => setDraft(event.target.value)} onKeyDown={event => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void send(); } }} /><button type={busy ? "button" : "submit"} disabled={connecting || loadingThread || (busy ? !turnId : !connected || !draft.trim())} onClick={busy ? () => void stopTurn() : undefined}>{busy ? "Stop" : "Send"}</button></form><small>{status}</small></footer>
    </main></div>
  </div>;
}
