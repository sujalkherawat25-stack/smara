import { useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import NativeSettings from "./NativeSettings";
import NativeVoice, { ReadAloud } from "./NativeVoice";
import { goalLabel, goalRequest, type Goal, type GoalList } from "./goalApi";
import NativeMarkdown from "./NativeMarkdown";
import NativeFilePreview from "./NativeFilePreview";
import NativeContextPicker from "./NativeContextPicker";
import { contextMessage, type ContextFile } from "./nativeContext";
import NativeTerminal from "./NativeTerminal";
import NativeChanges, { reviewRequest } from "./NativeChanges";
import NativeCommand from "./NativeCommand";
import { nativeSettings, sameWorkspace, type NativeSetup } from "./nativeApi";
import { NativeRpc, RpcOutcomeUnknown, type RpcMessage } from "./nativeRpc";
import { appendTerminalOutput, decodeTerminalOutput, encodeTerminalInput, terminalExecParams, type TerminalSession } from "./terminalProtocol";
import { activeTurn, answerKey, approvalResponse, displayItem, displayRuntimeError, mergeItem, mergeWorkers, workerActivity, workerSnapshot, resumeStatus, routeMessage, supportsApproval, threadLabel, type NativeTurn, type ViewItem, type WorkerView } from "./nativeView";
import type { LocalModelProfile } from "./types";
import packageInfo from "../package.json";
import "./native-shell.css";

type Item = ViewItem;
type Approval = { id: number | string; method: string; params: Record<string, unknown> };
type Question = { id: string; question: string; isSecret?: boolean; options?: { label: string; description: string }[] | null };
type Thread = { id: string; name?: string | null; preview?: string; updatedAt?: string | number; createdAt?: string | number };
type ChatSort = "recent" | "oldest";
const SLASH_COMMANDS = [
  { command: "/goal", label: "Autonomous goal", description: "Keep working in bounded cycles until the result is ready for your review." },
  { command: "/new", label: "New chat", description: "Start a fresh conversation." },
  { command: "/help", label: "Command help", description: "Show available Smara commands." },
];

function threadListOptions(workspace: string, sort: ChatSort, archived: boolean, cursor?: string | null) {
  return { limit: 50, cwd: workspace, cursor: cursor ?? undefined, archived,
    sortKey: sort === "recent" ? "updated_at" : "created_at", sortDirection: sort === "recent" ? "desc" : "asc" };
}

function goalTaskKind(prompt: string): "general" | "coding" | "research" | "mixed" {
  const value = prompt.toLowerCase();
  const research = /\b(research|analy[sz]e|analysis|investigat|sources?|market|buyers?|competitors?|latest|current|today|compare|report)\b/.test(value);
  const coding = /\b(code|coding|implement|fix|debug|tests?|build|refactor|program|software|repository|repo)\b/.test(value);
  return research && coding ? "mixed" : research ? "research" : coding ? "coding" : "general";
}

const GOAL_SUCCESS_CRITERIA = "Complete the requested outcome. Report evidence, completed work, checks performed, remaining gaps, and the next step. Do not claim completion without supporting evidence.";

function terminalError(reason: unknown): string {
  const message = String(reason);
  if (message.toLowerCase().includes("streaming command/exec is not supported with windows sandbox")) {
    return "This Windows runtime cannot stream a shell in its selected sandbox. No shell was started and Smara will not fall back to an unsandboxed process. Check MXC sandbox availability, then reconnect and retry.";
  }
  return message;
}

const NATIVE_DEVELOPER_INSTRUCTIONS = [
  "You are Smara. Be honest about tool outcomes. Do not claim tests passed without evidence. Ask before external writes, payments, publishing, or messaging people.",
  "Work output-first: use tools to create requested files instead of repeatedly narrating future actions. Give brief progress only when blocked, waiting, or needing approval. Never claim an artifact exists until you have created and verified it.",
  "For latest, current, today, news, or other time-sensitive questions, use current_time and web_search when those tools are available, then read relevant public source pages before answering. Do not pretend to have searched.",
  "Smara's web_search is built in; a separate Tavily MCP server is not required. Never inspect shell variables, credential files, or environment settings to find or reveal API keys, and never claim a key is missing based on shell probes. Never ask the user to paste a key into chat. If web_search is unavailable, direct them to Settings → Integrations → Web search. If the provider rejects the saved key, say so and direct them to replace it there.",
].join(" ");

export default function NativeApp() {
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [goalWorking, setGoalWorking] = useState(false);
  const [currentGoal, setCurrentGoal] = useState<Goal | null>(null);
  const [goalServiceRunning, setGoalServiceRunning] = useState(false);
  const [goalServiceStarting, setGoalServiceStarting] = useState(false);
  const [goalServiceError, setGoalServiceError] = useState("");
  const [goalPollError, setGoalPollError] = useState("");
  const [goalPending, setGoalPending] = useState(false);
  const [goalFeedback, setGoalFeedback] = useState("");
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
  const [terminalPlatformSupported, setTerminalPlatformSupported] = useState(false);
  const [threads, setThreads] = useState<Thread[]>([]);
  const [chatSort, setChatSort] = useState<ChatSort>("recent");
  const [showArchived, setShowArchived] = useState(false);
  const [threadMenu, setThreadMenu] = useState("");
  const [threadActionBusy, setThreadActionBusy] = useState("");
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [threadId, setThreadId] = useState("");
  const [turnId, setTurnId] = useState("");
  const [busy, setBusy] = useState(false);
  const [activityClock, setActivityClock] = useState(Date.now());
  const [turnStartedAt, setTurnStartedAt] = useState<number | null>(null);
  const [uncertain, setUncertain] = useState(false);
  const [toolsEnabled, setToolsEnabled] = useState(false);
  const [workersEnabled, setWorkersEnabled] = useState(false);
  const [workers, setWorkers] = useState<WorkerView[]>([]);
  const [browserOrigins, setBrowserOrigins] = useState("");
  const [draft, setDraft] = useState("");
  const [contextOpen, setContextOpen] = useState(false);
  const [contextFiles, setContextFiles] = useState<ContextFile[]>([]);
  const [items, setItems] = useState<Item[]>([]);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  const [status, setStatus] = useState("Preparing project…");
  const [panel, setPanel] = useState<"changes" | "terminal" | null>(null);
  const [previewPath, setPreviewPath] = useState<string | null>(null);
  const [terminalSession, setTerminalSession] = useState<TerminalSession | null>(null);
  const [reviewBusy, setReviewBusy] = useState(false);
  const [reviewRevision, setReviewRevision] = useState(0);
  const [reviewNotice, setReviewNotice] = useState("");
  const [turnDiff, setTurnDiff] = useState("");
  const [canContinueTurn, setCanContinueTurn] = useState(false);
  const [editingPrompt, setEditingPrompt] = useState(false);
  const [copiedItemId, setCopiedItemId] = useState("");
  const [slashSelection, setSlashSelection] = useState(0);
  const threadRef = useRef("");
  const clientRef = useRef<NativeRpc | null>(null);
  const terminalIdRef = useRef("");
  const terminalDecoderRef = useRef<TextDecoder | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const submitted = useRef("");
  const submittedFiles = useRef<ContextFile[]>([]);
  const projectDrafts = useRef<Record<string, string>>({});
  const projectContexts = useRef<Record<string, ContextFile[]>>({});
  const previousTurn = useRef("");
  const activeTurnRef = useRef("");
  const busyRef = useRef(false);
  const lifecycleRef = useRef(false);
  const loadingThreadRef = useRef(false);
  const restoreThreadRef = useRef("");
  const settingsPendingRef = useRef(false);
  const reviewBusyRef = useRef(false);
  const goalSubmittingRef = useRef(false);
  const goalReceiptRef = useRef<{ workspace: string; prompt: string; identity: string } | null>(null);
  const currentGoalRef = useRef<Goal | null>(null);
  const goalFingerprintRef = useRef("");
  const goalServiceStartedAtRef = useRef(0);
  const dismissedGoalsRef = useRef<Set<string>>(new Set());
  const streamDeltasRef = useRef(new Map<string, { kind: string; text: string }>());
  const terminalDeltasRef = useRef(new Map<string, { text: string; truncated: boolean }>());
  const streamDeltaTimerRef = useRef<number | null>(null);
  const scrollFrameRef = useRef<number | null>(null);
  const turnSnapshot = useRef<{ id: string; workspace: string; thread: string } | null>(null);

  const applyGoalSnapshot = (value: GoalList) => {
    const allGoals = value.goals || [];
    const goals = allGoals.filter(g => !dismissedGoalsRef.current.has(g.id));
    const activeGoal = value.current_goal && !dismissedGoalsRef.current.has(value.current_goal.id)
      ? value.current_goal
      : goals.find(goal => !["completed", "stopped"].includes(goal.state)) ?? null;
    const latest = activeGoal ?? (
      currentGoalRef.current && goals.some(g => g.id === currentGoalRef.current?.id)
        ? goals.find(g => g.id === currentGoalRef.current?.id) ?? null
        : null
    );
    setGoalWorking(allGoals.some(goal => !!goal.lease) || !!value.current_goal?.lease);
    setGoalServiceRunning(!!value.service.running);
    if (value.service.running) {
      setGoalServiceStarting(false); setGoalServiceError(""); setGoalPollError("");
      goalServiceStartedAtRef.current = 0;
    } else if (goalServiceStartedAtRef.current && Date.now() - goalServiceStartedAtRef.current > 30_000) {
      setGoalServiceStarting(false);
      setGoalServiceError("The background worker did not report ready. Retry it or restart Smara; your goal is still saved.");
      goalServiceStartedAtRef.current = 0;
    }
    if (!latest) {
      goalFingerprintRef.current = "";
      currentGoalRef.current = null;
      setCurrentGoal(current => current === null ? current : null);
      return;
    }
    const fingerprint = JSON.stringify([latest.id, latest.state, latest.lease, latest.cycles, latest.requests,
      latest.token_estimate, latest.detail, latest.updated, latest.checkpoint, latest.approvals?.map(item => item.id),
      latest.events?.at(-1)?.seq]);
    if (fingerprint !== goalFingerprintRef.current) {
      goalFingerprintRef.current = fingerprint;
      currentGoalRef.current = latest;
      setCurrentGoal(latest);
      const message = latest.state === "running" ? (latest.detail || `Working · cycle ${latest.cycles}`)
        : latest.state === "queued" ? (latest.detail || "Queued · waiting for the background worker")
        : latest.state === "awaiting_approval" ? "Waiting for your approval in Smara Desktop"
        : latest.detail || `Goal ${goalLabel(latest.state).toLowerCase()}`;
      setItems(current => {
        const id = `goal-start-${latest.id}`;
        const index = current.findIndex(item => item.id === id);
        if (index < 0 || current[index].text === message) return current;
        return current.map(item => item.id === id ? { ...item, text: message } : item);
      });
    }
  };

  const dismissGoal = async (id?: string) => {
    if (!id) {
      currentGoalRef.current = null;
      setCurrentGoal(null);
      return;
    }
    if (goalPending) return;
    setGoalPending(true);
    try {
      await goalRequest("delete", { id });
      dismissedGoalsRef.current.add(id);
      if (currentGoalRef.current?.id === id) {
        currentGoalRef.current = null;
        setCurrentGoal(null);
      }
      const value = await goalRequest<GoalList>("list", { workspace });
      applyGoalSnapshot(value);
      setError("");
    } catch (reason) {
      setError(String(reason));
    } finally {
      setGoalPending(false);
    }
  };

  useEffect(() => {
    let disposed = false, timer: number | undefined, pending = false;
    const poll = async () => {
      if (disposed || pending || !workspace) return;
      pending = true;
      try {
        const value = await goalRequest<GoalList>("list", { workspace });
        if (!disposed) { applyGoalSnapshot(value); setGoalPollError(""); }
      } catch {
        if (!disposed) setGoalPollError("Could not refresh task status. The saved task is safe; Smara will retry.");
      } finally {
        pending = false;
        if (!disposed) {
          const active = currentGoalRef.current && !["completed", "stopped"].includes(currentGoalRef.current.state);
          timer = setTimeout(() => void poll(), active ? 2500 : 15_000);
        }
      }
    };
    void poll();
    return () => { disposed = true; if (timer !== undefined) clearTimeout(timer); };
  }, [workspace]);

  useEffect(() => {
    if (!goalServiceStarting) return;
    const timer = setInterval(() => {
      if (goalServiceStartedAtRef.current && Date.now() - goalServiceStartedAtRef.current > 30_000) {
        setGoalServiceStarting(false);
        setGoalServiceError("The background worker did not report ready. Retry it or restart Smara; your goal is still saved.");
        goalServiceStartedAtRef.current = 0;
      }
    }, 1000);
    return () => clearInterval(timer);
  }, [goalServiceStarting]);

  const goalAction = async (action: string, values: Record<string, unknown> = {}) => {
    if (goalPending) return false;
    setGoalPending(true);
    try {
      if (action === "start_service") {
        const service = await goalRequest<{ running?: boolean; starting?: boolean }>(action, values);
        setGoalServiceRunning(!!service.running);
        setGoalServiceStarting(!!service.starting);
        setGoalServiceError(service.running || service.starting ? "" : "Background worker is not running.");
        goalServiceStartedAtRef.current = service.starting ? Date.now() : 0;
      } else await goalRequest(action, values);
      const value = await goalRequest<GoalList>("list", { workspace });
      applyGoalSnapshot(value);
      setError("");
      return true;
    } catch (reason) {
      if (action === "start_service") setGoalServiceError(String(reason));
      else setError(String(reason));
      return false;
    }
    finally { setGoalPending(false); }
  };

  const answerGoalQuestions = async (goalId: string, requestId: string | number, questions: Question[]) => {
    if (!Array.isArray(questions) || !questions.length) { setError("This native request has no answerable questions."); return; }
    if (questions.some(question => !answers[answerKey(requestId, question.id)]?.trim())) {
      setError("Answer each question before continuing.");
      return;
    }
    const values = Object.fromEntries(questions.map(question => [question.id,
      { answers: [answers[answerKey(requestId, question.id)]] }]));
    if (await goalAction("respond", { id: goalId, request_id: requestId, answers: values })) {
      setAnswers(current => Object.fromEntries(Object.entries(current).filter(([key]) =>
        !questions.some(question => key === answerKey(requestId, question.id)))));
    }
  };

  const submitGoalFeedback = async (goalId: string) => {
    const text = goalFeedback.trim();
    if (!text) return;
    if (await goalAction("feedback", { id: goalId, text })) setGoalFeedback("");
  };

  const extendAndResumeGoal = async (g: Goal) => {
    if (goalPending) return;
    setGoalPending(true);
    try {
      const nextTokens = Math.max((g.token_estimate || 0) + 2_000_000, (g.spec?.max_token_estimate || 500_000) + 2_000_000);
      const nextRequests = Math.max((g.requests || 0) + 30, (g.spec?.max_requests || 30) + 30);
      const nextCycles = Math.max((g.cycles || 0) + 8, (g.spec?.max_cycles || 8) + 8);
      await goalRequest("extend", { id: g.id, max_token_estimate: nextTokens, max_requests: nextRequests, max_cycles: nextCycles });
      await goalRequest("resume", { id: g.id });
      const value = await goalRequest<GoalList>("list", { workspace });
      applyGoalSnapshot(value);
      setError("");
    } catch (reason) {
      setError(String(reason));
    } finally {
      setGoalPending(false);
    }
  };

  const startGoal = async (rawPrompt: string) => {
    const prompt = rawPrompt.replace(/^\/goal\s*/i, "").trim();
    if (!prompt) { setError("Add the goal prompt after /goal."); return; }
    if (!workspace || !profile) { setError("Choose a project and model before starting a goal."); return; }
    const taskKind = goalTaskKind(prompt);
    // A prompt that explicitly asks for coding work must be able to produce
    // edits; the native workspace sandbox and on-request approvals still apply.
    const sandbox = taskKind === "coding" || taskKind === "mixed" ? "workspace-write" : "read-only";
    if (goalSubmittingRef.current) return;
    goalSubmittingRef.current = true;
    setGoalPending(true);
    if (!goalReceiptRef.current || goalReceiptRef.current.workspace !== workspace || goalReceiptRef.current.prompt !== prompt) {
      goalReceiptRef.current = { workspace, prompt, identity: crypto.randomUUID().replaceAll("-", "") };
    }
    const identity = goalReceiptRef.current.identity;
    try {
      if (!connected && !await connect()) throw new Error("Connect to the selected project before starting this goal.");
      if (!threadRef.current) {
        const client = clientRef.current;
        if (!client) throw new Error("The native chat session is not ready.");
        const result = await client.request<{ thread: Thread }>("thread/start", {
          cwd: workspace, approvalPolicy: "on-request", sandbox: "workspace-write", developerInstructions: NATIVE_DEVELOPER_INSTRUCTIONS,
        });
        threadRef.current = result.thread.id;
        setThreadId(result.thread.id);
        setThreads(current => [result.thread, ...current.filter(thread => thread.id !== result.thread.id)]);
        void client.request("thread/name/set", { threadId: result.thread.id, name: prompt.slice(0, 72) }).catch(() => {});
      }
      const selectedProject = setup?.preferences.projects.find(item => sameWorkspace(item.workspace, workspace));
      const created = await goalRequest<Goal>("create", {
        identity, workspace, profile_id: profile, objective: prompt, origin_thread_id: threadRef.current,
        success_criteria: GOAL_SUCCESS_CRITERIA, task_kind: taskKind,
        sandbox, options: selectedProject?.options ?? {},
      });
      goalReceiptRef.current = null;
      goalFingerprintRef.current = "";
      currentGoalRef.current = created;
      setCurrentGoal(created);
      setDraft(value => value.trim() === rawPrompt.trim() ? "" : value);
      setItems(value => [...value,
        { id: `goal-user-${created.id}`, kind: "user", text: prompt },
        { id: `goal-start-${created.id}`, kind: "assistant", text: "Queued · starting the background worker…" },
      ]);
      try {
        const service = await goalRequest<{ running?: boolean; starting?: boolean }>("start_service");
        setGoalServiceRunning(!!service.running);
        setGoalServiceStarting(!!service.starting);
        setGoalServiceError(service.running || service.starting ? "" : "Background worker is not running.");
        goalServiceStartedAtRef.current = service.starting ? Date.now() : 0;
        applyGoalSnapshot(await goalRequest<GoalList>("list", { workspace }));
        setError("");
      } catch (reason) {
        setGoalServiceRunning(false); setGoalServiceStarting(false);
        setGoalServiceError(`Goal saved, but background work did not start: ${String(reason)}`);
      }
    } catch (reason) { setError(String(reason)); }
    finally { goalSubmittingRef.current = false; setGoalPending(false); }
  };

  const reviewPending = (pending: boolean) => { reviewBusyRef.current = pending; setReviewBusy(pending); };
  const sealTurn = async () => {
    const snapshot = turnSnapshot.current;
    if (!snapshot) { setReviewRevision(value => value + 1); return; }
    turnSnapshot.current = null;
    reviewPending(true);
    try {
      await reviewRequest(snapshot.workspace, "seal", { snapshot: snapshot.id });
      setReviewNotice("");
    } catch (reason) { setReviewNotice(`Last-turn Undo is unavailable for this run: ${String(reason)}. Current Git changes remain reviewable.`); }
    finally { reviewPending(false); setReviewRevision(value => value + 1); }
  };

  const updateItem = (id: string, kind: string, text: string, append = false, itemStatus?: string) => {
    setItems(current => mergeItem(current, { id, kind, text, status: itemStatus }, append));
  };
  const flushStreamDeltas = () => {
    if (streamDeltaTimerRef.current !== null) {
      clearTimeout(streamDeltaTimerRef.current);
      streamDeltaTimerRef.current = null;
    }
    const itemDeltas = [...streamDeltasRef.current.entries()];
    streamDeltasRef.current.clear();
    const terminalDeltas = [...terminalDeltasRef.current.entries()];
    terminalDeltasRef.current.clear();
    if (itemDeltas.length) setItems(current => itemDeltas.reduce((items, [id, value]) =>
      mergeItem(items, { id, kind: value.kind, text: value.text }, true), current));
    for (const [id, value] of terminalDeltas) setTerminalSession(current =>
      current?.id === id ? appendTerminalOutput(current, value.text, value.truncated) : current);
  };
  const scheduleStreamFlush = () => {
    if (streamDeltaTimerRef.current === null) {
      // Coalesce bursty token/output IPC events into at most ~13 UI updates/s.
      streamDeltaTimerRef.current = setTimeout(flushStreamDeltas, 75);
    }
  };
  const queueItemDelta = (id: string, kind: string, text: string) => {
    if (!id || !text) return;
    const previous = streamDeltasRef.current.get(id);
    streamDeltasRef.current.set(id, { kind, text: (previous?.text ?? "") + text });
    scheduleStreamFlush();
  };
  const queueTerminalDelta = (id: string, text: string, truncated: boolean) => {
    if (!id || !text) return;
    const previous = terminalDeltasRef.current.get(id);
    terminalDeltasRef.current.set(id, { text: (previous?.text ?? "") + text, truncated: truncated || previous?.truncated === true });
    scheduleStreamFlush();
  };
  const copyMessage = async (item: Item) => {
    try {
      await navigator.clipboard.writeText(item.text);
      setCopiedItemId(item.id);
      setTimeout(() => setCopiedItemId(current => current === item.id ? "" : current), 1600);
    } catch { setError("Clipboard is unavailable"); }
  };
  const editMessage = (text: string) => {
    setDraft(text); setEditingPrompt(true);
    requestAnimationFrame(() => composerRef.current?.focus());
  };
  const accepted = () => {
    const text = submitted.current;
    if (text) {
      setDraft(current => current === text ? "" : current);
      const sentPaths = new Set(submittedFiles.current.map(file => file.path));
      setContextFiles(current => current.filter(file => !sentPaths.has(file.path)));
      setThreads(current => current.map(thread => thread.id === threadRef.current && !thread.preview ? { ...thread, preview: text } : thread));
    }
    submitted.current = ""; submittedFiles.current = []; setUncertain(false); setEditingPrompt(false);
  };

  const receive = (message: RpcMessage) => {
    const params = message.params ?? {};
    if (["item/started", "item/completed", "turn/completed", "smara/runtimeStopped"].includes(message.method ?? "")) flushStreamDeltas();
    if (message.method === "command/exec/outputDelta") {
      const processId = String(params.processId ?? "");
      if (!processId || processId !== terminalIdRef.current) return;
      try {
        const delta = decodeTerminalOutput(String(params.deltaBase64 ?? ""), terminalDecoderRef.current ?? new TextDecoder());
        queueTerminalDelta(processId, delta, params.capReached === true);
      } catch {
        setTerminalSession(current => current?.id === processId ? { ...current, error: "Terminal output could not be decoded." } : current);
      }
      return;
    }
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
    if (route === "ignore") {
      setWorkers(current => workerActivity(current, message, Date.now()));
      return;
    }
    if (message.method === "turn/diff/updated") {
      setTurnDiff(String(params.diff ?? ""));
    } else if (message.method === "item/agentMessage/delta") {
      queueItemDelta(String(params.itemId), "assistant", String(params.delta ?? ""));
    } else if (message.method === "item/commandExecution/outputDelta") {
      queueItemDelta(String(params.itemId), "commandExecution", String(params.delta ?? ""));
    } else if (message.method === "item/started" || message.method === "item/completed") {
      const item = params.item as Record<string, unknown> | undefined;
      if (!item) return;
      setWorkers(current => mergeWorkers(current, item));
      setItems(current => mergeItem(current, displayItem(item), false, message.method === "item/started"));
    } else if (message.method === "turn/started") {
      const turn = params.turn as { id: string };
      setCanContinueTurn(false);
      accepted(); activeTurnRef.current = turn.id; busyRef.current = true;
      setTurnId(turn.id); setBusy(true); setTurnStartedAt(Date.now()); setStatus("Working");
    } else if (message.method === "turn/completed") {
      const turn = params.turn as { id: string; status: string; error?: { message?: string } };
      if (activeTurnRef.current && turn.id !== activeTurnRef.current) return;
      if (!activeTurnRef.current && submitted.current && turn.id === previousTurn.current) return;
      previousTurn.current = turn.id; activeTurnRef.current = ""; busyRef.current = false;
      setCanContinueTurn(turn.status === "interrupted" || turn.status === "failed");
      accepted(); setBusy(false); setTurnId("");
      // A parent completing must not discard a worker's outstanding request.
      // Native serverRequest/resolved is the authority for request lifetime.
      setApprovals(current => current.filter(item => !(item.params.threadId === params.threadId && item.params.turnId === turn.id)));
      setStatus(turn.status);
      if (turn.status !== "completed" && turn.error?.message) setError(displayRuntimeError(turn.error.message));
      void sealTurn();
    } else if (message.method === "error" || message.method === "smara/runtimeError") {
      setError(displayRuntimeError(params.message ?? (params.error as { message?: string })?.message ?? "Native runtime error"));
    } else if (message.method === "smara/runtimeStopped") {
      activeTurnRef.current = ""; busyRef.current = false;
      setConnected(false); setBusy(false); setUncertain(false); setApprovals([]); setStatus("Runtime stopped; unfinished work is not reported as complete");
      setTerminalSession(current => current && ["starting", "running", "stopping"].includes(current.status) ? { ...current, status: "stopped", error: "The native runtime disconnected; its shell was stopped too." } : current);
    }
  };

  useEffect(() => {
    const trackGoal = !!currentGoal && !["completed", "stopped"].includes(currentGoal.state);
    if (!busy && !goalWorking && !goalServiceStarting && !trackGoal) return;
    const timer = setInterval(() => setActivityClock(Date.now()), busy ? 1000 : 15_000);
    return () => clearInterval(timer);
  }, [busy, goalWorking, goalServiceStarting, currentGoal?.id, currentGoal?.state]);

  useEffect(() => () => {
    if (streamDeltaTimerRef.current !== null) clearTimeout(streamDeltaTimerRef.current);
    if (scrollFrameRef.current !== null) cancelAnimationFrame(scrollFrameRef.current);
  }, []);

  // Some app-server clients are not subscribed to child transcript events.
  // Read only the latest turn summary, not all historical tool outputs. Never
  // resume a child, resend work, or overlap polling rounds.
  const workerIds = workers.map(worker => worker.id).join(",");
  useEffect(() => {
    const client = clientRef.current;
    if (!connected || !busy || !client || !workerIds) return;
    const generation = client.generation, parent = threadRef.current;
    let disposed = false, pending = false;
    const poll = async () => {
      if (disposed || pending || !client.generation) return;
      pending = true;
      try {
        for (const id of workerIds.split(",")) {
          if (disposed) break;
          try {
            const result = await client.request<{ data: NativeTurn[] }>("thread/turns/list", { threadId: id, limit: 1, sortDirection: "desc", itemsView: "summary" }, 10000);
            if (disposed || client !== clientRef.current || client.generation !== generation || parent !== threadRef.current) return;
            const turn = result.data?.[0];
            if (turn) setWorkers(current => workerSnapshot(current, id, turn, Date.now()));
          } catch {
            if (!disposed) setWorkers(current => current.map(worker => worker.id === id ? { ...worker, activity: "Status unavailable — use Check status" } : worker));
          }
        }
      } finally { pending = false; }
    };
    void poll();
    const timer = setInterval(() => void poll(), 15000);
    return () => { disposed = true; clearInterval(timer); };
  }, [connected, busy, threadId, workerIds]);

  useEffect(() => {
    let disposed = false;
    void Promise.all([nativeSettings(), invoke<{ built: boolean; windowsTerminalBuild?: boolean }>("native_status")])
      .then(([initial, native]) => {
        if (disposed) return;
        setSetup(initial); setWorkspace(initial.preferences.workspace); setProfiles(initial.profiles); setBuilt(native.built);
        const options = initial.preferences.projects.find(project => sameWorkspace(project.workspace, initial.preferences.workspace))?.options;
        setToolsEnabled(options?.tools_enabled ?? false); setWorkersEnabled(options?.workers_enabled ?? false); setBrowserOrigins(options?.browser_origins.join(" ") ?? "");
        setTerminalPlatformSupported(native.windowsTerminalBuild === true);
        const selected = initial.preferences.active_model;
        const configured = initial.profiles.some(model => model.id === selected);
        setProfile(configured ? selected : "");
        setStatus(configured ? "Ready" : "Choose a model in Settings to get started");
      }).catch(reason => { if (!disposed) setError(String(reason)); });
    return () => { disposed = true; void clientRef.current?.stop().catch(() => { /* App exit also owns native cleanup. */ }); };
  }, []);
  useEffect(() => {
    const pane = bottom.current?.parentElement;
    if (pane && pane.scrollHeight - pane.scrollTop - pane.clientHeight < 250 && scrollFrameRef.current === null) {
      scrollFrameRef.current = requestAnimationFrame(() => {
        scrollFrameRef.current = null;
        bottom.current?.scrollIntoView({ block: "end" });
      });
    }
  }, [items, busy, approvals.length]);

  const connect = async () => {
    if (lifecycleRef.current || busyRef.current || loadingThreadRef.current || reviewBusyRef.current) return null;
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
      const list = await client.request<{ data: Thread[]; nextCursor?: string | null }>("thread/list", threadListOptions(workspace, chatSort, showArchived));
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
  const startTerminal = async () => {
    if (!terminalPlatformSupported) return;
    const client = clientRef.current;
    if (!connected || !client?.generation || (terminalSession && ["starting", "running", "stopping"].includes(terminalSession.status))) return;
    const processId = `smara-terminal-${crypto.randomUUID()}`;
    terminalIdRef.current = processId;
    terminalDecoderRef.current = new TextDecoder();
    setTerminalSession({ id: processId, status: "starting", output: "", truncated: false });
    try {
      const readiness = await client.request<{ status?: string }>("windowsSandbox/readiness", {});
      if (client !== clientRef.current || client.generation === "") throw new Error("The native runtime disconnected before the shell could start.");
      if (readiness.status !== "ready") throw new Error(`Windows sandbox is ${readiness.status ?? "unavailable"}. Configure the native Windows sandbox before starting an interactive shell.`);
      const running = client.request<{ exitCode?: number }>("command/exec", terminalExecParams(workspace, processId), null);
      setTerminalSession(current => current?.id === processId ? { ...current, status: "running" } : current);
      void running.then(result => {
        const tail = terminalDecoderRef.current ? decodeTerminalOutput("", terminalDecoderRef.current, false) : "";
        terminalDecoderRef.current = null;
        setTerminalSession(current => current?.id === processId
          ? { ...appendTerminalOutput(current, tail), status: "exited", exitCode: result.exitCode ?? 0 }
          : current);
      }).catch(reason => {
        terminalDecoderRef.current = null;
        setTerminalSession(current => current?.id === processId
          ? { ...current, status: client.generation ? "failed" : "stopped", error: terminalError(reason) }
          : current);
      });
    } catch (reason) {
      terminalDecoderRef.current = null;
      setTerminalSession(current => current?.id === processId ? { ...current, status: "failed", error: terminalError(reason) } : current);
    }
  };
  const writeTerminal = async (value: string) => {
    const client = clientRef.current;
    const processId = terminalIdRef.current;
    if (!client || !processId || !terminalSession || terminalSession.status !== "running") return;
    try { await client.request("command/exec/write", { processId, deltaBase64: encodeTerminalInput(value) }); }
    catch (reason) { setTerminalSession(current => current?.id === processId ? { ...current, error: String(reason) } : current); }
  };
  const interruptTerminal = () => { void writeTerminal("\u0003"); };
  const stopTerminal = async () => {
    const client = clientRef.current;
    const processId = terminalIdRef.current;
    if (!client || !processId || terminalSession?.status !== "running") return;
    setTerminalSession(current => current?.id === processId ? { ...current, status: "stopping" } : current);
    try { await client.request("command/exec/terminate", { processId }); }
    catch (reason) { setTerminalSession(current => current?.id === processId ? { ...current, status: "failed", error: String(reason) } : current); }
  };
  const resizeTerminal = (cols: number, rows: number) => {
    const client = clientRef.current;
    const processId = terminalIdRef.current;
    if (!client || !processId || terminalSession?.status !== "running") return;
    void client.request("command/exec/resize", { processId, size: { cols, rows } }).catch(() => {});
  };
  const terminalActive = !!terminalSession && ["starting", "running", "stopping"].includes(terminalSession.status);
  const loadThread = async (client: NativeRpc, id: string) => {
    const generation = client.generation;
    loadingThreadRef.current = true; setLoadingThread(true); setError(""); setStatus("Loading saved conversation…");
    try {
      const saved = await client.request<{ thread: { cwd: string } }>("thread/read", { threadId: id });
      if (!sameWorkspace(saved.thread.cwd, workspace)) throw new Error("This conversation belongs to another project. Open that project to resume it.");
      const response = await client.request<{ thread: { id: string; turns: NativeTurn[] } }>("thread/resume", { threadId: id, model: profiles.find(model => model.id === profile)?.model, modelProvider: "smara_chat_adapter", approvalPolicy: "on-request", sandbox: "workspace-write" });
      if (client !== clientRef.current || client.generation !== generation) return false;
      threadRef.current = id; setThreadId(id); setItems([]);
      setTurnDiff(""); setReviewNotice("");
      restoreThreadRef.current = "";
      setItems((response.thread.turns ?? []).flatMap(turn => turn.items.map(displayItem)));
      setWorkers((response.thread.turns ?? []).flatMap(turn => turn.items).reduce(mergeWorkers, [] as WorkerView[]));
      previousTurn.current = response.thread.turns.at(-1)?.id ?? "";
      const active = activeTurn(response.thread.turns ?? []);
      const latest = response.thread.turns.at(-1);
      setCanContinueTurn(!active && (latest?.status === "interrupted" || latest?.status === "failed"));
      activeTurnRef.current = active?.id ?? ""; busyRef.current = !!active;
      setBusy(!!active); setTurnId(active?.id ?? "");
      setStatus(resumeStatus(response.thread.turns ?? []));
      if (!active && turnSnapshot.current?.thread === id) void sealTurn();
      return true;
    } catch (reason) { if (client === clientRef.current && client.generation === generation) setError(String(reason)); return false; }
    finally { loadingThreadRef.current = false; setLoadingThread(false); }
  };
  const selectThread = async (id: string) => {
    if (busyRef.current || lifecycleRef.current || loadingThreadRef.current || reviewBusyRef.current || !connected) return;
    await loadThread(clientRef.current!, id);
  };
  const openGoalConversation = async () => {
    const id = currentGoal?.spec?.origin_thread_id;
    if (!id) return;
    const client = connected ? clientRef.current : await connect();
    if (client) await loadThread(client, id);
  };
  const send = async (overrideText?: string) => {
    const textToSend = overrideText ?? draft;
    if (/^\/new\s*$/i.test(textToSend.trim())) {
      setDraft(""); newConversation(); return;
    }
    if (/^\/help\s*$/i.test(textToSend.trim())) {
      setItems(current => [...current, { id: `local-help-${crypto.randomUUID()}`, kind: "assistant",
        text: "Available commands:\n- `/goal <outcome>` — run bounded background cycles and review the result here.\n- `/new` — start a fresh chat.\n- `/help` — show this list." }]);
      setDraft(""); setError(""); return;
    }
    if (textToSend.trim().startsWith("/") && !/^\/goal(?:\s|$)/i.test(textToSend.trim())) {
      setError("Unknown command. Type / to choose a Smara command."); return;
    }
    if (/^\/goal(?:\s|$)/i.test(textToSend.trim())) {
      if (busyRef.current || lifecycleRef.current || loadingThreadRef.current || reviewBusyRef.current || approvals.length || connecting || goalPending || built !== true) return;
      await startGoal(textToSend);
      return;
    }
    if (!textToSend.trim() || goalWorking || busyRef.current || lifecycleRef.current || loadingThreadRef.current || reviewBusyRef.current || approvals.length || !profile || built !== true) return;
    const filesToSend = [...contextFiles];
    if (!connected && !await connect()) return;
    // A failed history restoration must never turn a reply into a new chat.
    if (restoreThreadRef.current || busyRef.current) return;
    busyRef.current = true;
    setError(""); setBusy(true); submitted.current = textToSend; submittedFiles.current = filesToSend;
    try {
      let id = threadRef.current;
      if (!id) {
        const result = await clientRef.current!.request<{ thread: Thread }>("thread/start", { cwd: workspace, approvalPolicy: "on-request", sandbox: "workspace-write", developerInstructions: NATIVE_DEVELOPER_INSTRUCTIONS });
        id = result.thread.id; threadRef.current = id; setThreadId(id);
        setThreads(current => [result.thread, ...current]);
      }
      const text = contextMessage(textToSend, filesToSend);
      setTurnDiff(""); setReviewNotice("");
      // Capture before acceptance, locally only. A non-Git workspace or an
      // unavailable snapshot must not prevent ordinary conversation.
      try {
        const baseline = await reviewRequest<{ snapshot: string; excluded: number }>(workspace, "capture", { thread_id: id });
        if (typeof baseline.snapshot === "string") {
          turnSnapshot.current = { id: baseline.snapshot, workspace, thread: id };
          if (baseline.excluded) setReviewNotice(`${baseline.excluded} unsupported files are excluded from turn Undo.`);
        }
      } catch (reason) { turnSnapshot.current = null; setReviewNotice(`Turn Undo unavailable: ${String(reason)}`); }
      // Keep the draft until native confirms acceptance. A timeout is not
      // permission to repeat a potentially accepted turn automatically.
      await clientRef.current!.request("turn/start", { threadId: id, input: [{ type: "text", text, text_elements: [] }] });
      accepted();
    } catch (reason) {
      setError(String(reason));
      if (reason instanceof RpcOutcomeUnknown) {
        if (submitted.current) { setUncertain(true); setStatus("Acceptance unknown — check status or recover chat before retrying. No automatic resend."); }
      } else { busyRef.current = false; setBusy(false); submitted.current = ""; submittedFiles.current = []; turnSnapshot.current = null; }
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
        setCanContinueTurn(!!latest && (latest.status === "interrupted" || latest.status === "failed"));
        setStatus("No new turn confirmed. Send stays locked; use Recover chat."); return;
      }
      accepted(); previousTurn.current = latest?.id ?? previousTurn.current;
      setCanContinueTurn(!current && (latest?.status === "interrupted" || latest?.status === "failed"));
      setItems(turns.flatMap(turn => turn.items.map(displayItem)));
      setWorkers(turns.flatMap(turn => turn.items).reduce(mergeWorkers, [] as WorkerView[]));
      setBusy(!!current); setTurnId(current?.id ?? "");
      busyRef.current = !!current; activeTurnRef.current = current?.id ?? "";
      setStatus(current ? "Working — native status confirmed" : latest?.status ?? "Ready");
      if (!current) void sealTurn();
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
  const slashMatch = /^\/([^\s]*)$/.exec(draft.trimStart());
  const slashOptions = slashMatch
    ? SLASH_COMMANDS.filter(item => item.command.slice(1).startsWith(slashMatch[1].toLowerCase()))
    : [];
  const chooseSlashCommand = (command: string) => {
    const value = `${command} `;
    setDraft(value); setError(""); setSlashSelection(0);
    requestAnimationFrame(() => {
      const input = composerRef.current;
      if (input) { input.focus(); input.setSelectionRange(value.length, value.length); }
    });
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
    if (busyRef.current || lifecycleRef.current || loadingThreadRef.current || settingsPendingRef.current || reviewBusyRef.current || approvals.length) return;
    setThreadId(""); threadRef.current = ""; restoreThreadRef.current = ""; setItems([]); setWorkers([]); setCanContinueTurn(false); setEditingPrompt(false); setError(""); setSettingsOpen(false); setTurnDiff(""); setReviewNotice(""); setPreviewPath(null); setReviewRevision(value => value + 1);
  };
  const switchProject = async (path: string, add = false) => {
    if (busyRef.current || lifecycleRef.current || loadingThreadRef.current || settingsPendingRef.current || reviewBusyRef.current || approvals.length || workers.some(worker => ["running", "pendingInit", "inProgress"].includes(worker.status))) return;
    if (connected && !await disconnect()) return;
    lifecycleRef.current = true; setConnecting(true); setError("");
    try {
      projectDrafts.current[workspace] = draft;
      projectContexts.current[workspace] = contextFiles;
      const next = await nativeSettings(add ? "add_project" : "select_project", { workspace: path, label: projectName });
      updateSetup(next); setWorkspace(next.preferences.workspace); setThreads([]); setNextCursor(null); setItems([]); setWorkers([]);
      restoreThreadRef.current = ""; threadRef.current = ""; setThreadId("");
      const options = next.preferences.projects.find(project => sameWorkspace(project.workspace, next.preferences.workspace))?.options;
      setToolsEnabled(options?.tools_enabled ?? false); setWorkersEnabled(options?.workers_enabled ?? false); setBrowserOrigins(options?.browser_origins.join(" ") ?? ""); setDraft(projectDrafts.current[next.preferences.workspace] ?? "");
      setContextFiles(projectContexts.current[next.preferences.workspace] ?? []); setContextOpen(false);
      setAddingProject(false); setProjectPath(""); setProjectName(""); setSettingsOpen(false);
      setPanel(null); setTurnDiff(""); setReviewNotice(""); setPreviewPath(null);
      setStatus("Project selected");
    } catch (reason) { setError(String(reason)); }
    finally { lifecycleRef.current = false; setConnecting(false); }
  };
  const selectModel = async (id: string) => {
    if (busyRef.current || lifecycleRef.current || loadingThreadRef.current || settingsPendingRef.current || reviewBusyRef.current || approvals.length) return;
    if (id === profile) return;
    if (connected && !await disconnect(true)) return;
    lifecycleRef.current = true; setConnecting(true); setError("");
    try { updateSetup(await nativeSettings("select_model", { id })); setProfile(id); }
    catch (reason) { setError(String(reason)); }
    finally { lifecycleRef.current = false; setConnecting(false); }
  };
  const project = setup?.preferences.projects.find(p => sameWorkspace(p.workspace, workspace));
  const navigationLocked = busy || connecting || loadingThread || settingsSaving || reviewBusy || approvals.length > 0 || workers.some(worker => ["running", "pendingInit", "inProgress"].includes(worker.status));
  const toggleSettings = async () => {
    if (navigationLocked || lifecycleRef.current || settingsPendingRef.current) return;
    if (settingsOpen) { setSettingsOpen(false); return; }
    // Pause the idle transport internally so credentials can be edited safely.
    // The selected conversation is restored, never resent, on return to chat.
    if (connected && !await disconnect(true)) return;
    setSettingsOpen(true);
  };
  const goalInThisConversation = !!currentGoal && (!currentGoal.spec?.origin_thread_id || currentGoal.spec?.origin_thread_id === threadId);
  const goalInAnotherConversation = !!currentGoal?.spec?.origin_thread_id
    && currentGoal.spec?.origin_thread_id !== threadId
    && !["completed", "stopped"].includes(currentGoal.state);
  const recoverSession = async () => {
    if (lifecycleRef.current || loadingThreadRef.current) return;
    if (await disconnect(true)) await connect();
  };
  const refreshChats = async (sort: ChatSort = chatSort, archived = showArchived) => {
    const client = clientRef.current;
    if (!client || !connected) return;
    const generation = client.generation;
    setLoadingMore(true); setNextCursor(null);
    try {
      const list = await client.request<{ data: Thread[]; nextCursor?: string | null }>("thread/list", threadListOptions(workspace, sort, archived));
      if (client !== clientRef.current || client.generation !== generation) return;
      setThreads(list.data); setNextCursor(list.nextCursor ?? null); setThreadMenu("");
    } catch (reason) { setError(String(reason)); }
    finally { setLoadingMore(false); }
  };
  const loadOlder = async () => {
    const client = clientRef.current;
    if (!client || !connected || !nextCursor || loadingMore || navigationLocked) return;
    const generation = client.generation;
    setLoadingMore(true);
    try {
      const list = await client.request<{ data: Thread[]; nextCursor?: string | null }>("thread/list", threadListOptions(workspace, chatSort, showArchived, nextCursor));
      if (client !== clientRef.current || client.generation !== generation) return;
      setThreads(current => [...current, ...list.data.filter(thread => !current.some(t => t.id === thread.id))]);
      setNextCursor(list.nextCursor ?? null);
    } catch (reason) { setError(String(reason)); }
    finally { setLoadingMore(false); }
  };
  const archiveChat = async (thread: Thread, archived: boolean) => {
    const client = clientRef.current;
    if (!client || !connected || navigationLocked || threadActionBusy) return;
    setThreadActionBusy(thread.id);
    try {
      await client.request(archived ? "thread/unarchive" : "thread/archive", { threadId: thread.id });
      if (threadRef.current === thread.id) newConversation();
      setThreads(current => current.filter(item => item.id !== thread.id));
      setThreadMenu("");
      await refreshChats();
    } catch (reason) { setError(String(reason)); }
    finally { setThreadActionBusy(""); }
  };
  const deleteChat = async (thread: Thread) => {
    const client = clientRef.current;
    if (!client || !connected || navigationLocked || threadActionBusy) return;
    const confirmed = window.confirm(`Permanently delete “${threadLabel(thread)}” and its spawned agent conversations? This cannot be undone.`);
    if (!confirmed) return;
    setThreadActionBusy(thread.id);
    try {
      await client.request("thread/delete", { threadId: thread.id });
      if (threadRef.current === thread.id) newConversation();
      setThreads(current => current.filter(item => item.id !== thread.id));
      setThreadMenu("");
      await refreshChats();
    } catch (reason) { setError(String(reason)); }
    finally { setThreadActionBusy(""); }
  };
  const renameChat = async (thread: Thread) => {
    const client = clientRef.current;
    if (!client || !connected || navigationLocked || threadActionBusy) return;
    const name = window.prompt("Conversation name", thread.name || thread.preview || "");
    if (name === null || !name.trim()) return;
    setThreadActionBusy(thread.id);
    try {
      await client.request("thread/name/set", { threadId: thread.id, name: name.trim().slice(0, 120) });
      setThreads(current => current.map(item => item.id === thread.id ? { ...item, name: name.trim().slice(0, 120) } : item));
      setThreadMenu("");
    } catch (reason) { setError(String(reason)); }
    finally { setThreadActionBusy(""); }
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
      <div className="sidebar-section-heading chat-list-heading"><span>{showArchived ? "Archived" : "Chats"}</span><select aria-label="Sort conversations" value={chatSort} disabled={!connected || loadingMore} onChange={event => { const value = event.target.value as ChatSort; setChatSort(value); void refreshChats(value, showArchived); }}><option value="recent">Recent</option><option value="oldest">Oldest</option></select><button aria-label={showArchived ? "Show active chats" : "Show archived chats"} title={showArchived ? "Active chats" : "Archived chats"} disabled={!connected || loadingMore} onClick={() => { const value = !showArchived; setShowArchived(value); void refreshChats(chatSort, value); }}>{showArchived ? "↩" : "▱"}</button></div>
      <div className="sidebar-threads">{threads.map(thread => <div className="sidebar-thread-row" key={thread.id}>
        <button className={`sidebar-thread-select ${thread.id === threadId ? "selected" : ""}`} title={thread.name || thread.preview || "New conversation"} disabled={navigationLocked || !connected} onClick={() => { setSettingsOpen(false); setThreadMenu(""); void selectThread(thread.id); }}>{threadLabel(thread)}</button>
        <button className="chat-menu-trigger" aria-label={`Actions for ${threadLabel(thread)}`} title="Conversation actions" disabled={!connected || !!threadActionBusy || navigationLocked} onClick={() => setThreadMenu(current => current === thread.id ? "" : thread.id)}>⋯</button>
        {threadMenu === thread.id && <div className="sidebar-thread-actions">
          <button disabled={!!threadActionBusy || navigationLocked} onClick={() => void renameChat(thread)}>Rename</button>
          {showArchived ? <><button disabled={!!threadActionBusy || navigationLocked} onClick={() => void archiveChat(thread, true)}>Restore</button><button className="danger-action" disabled={!!threadActionBusy || navigationLocked} onClick={() => void deleteChat(thread)}>Delete permanently…</button></> : <button disabled={!!threadActionBusy || navigationLocked} onClick={() => void archiveChat(thread, false)}>Archive</button>}
        </div>}
      </div>)}
      {!threads.length && <p>{loadingMore ? "Loading chats…" : showArchived ? "No archived conversations." : connecting ? "Loading chats…" : "Your chats appear here."}</p>}{nextCursor && <button disabled={navigationLocked || loadingMore || !connected} onClick={() => void loadOlder()}>{loadingMore ? "Loading…" : "Show more"}</button>}</div>
      <div className="sidebar-bottom"><button disabled={navigationLocked} className={settingsOpen ? "selected" : ""} onClick={() => void toggleSettings()}>⚙ Settings</button></div>
    </aside><main>
      <header className="native-topbar"><span className="project-icon" aria-hidden="true">▱</span><strong title={workspace}>{settingsOpen ? "Settings" : project?.label || "Choose a project"}</strong>{!settingsOpen && threadId && <span className="header-chat-title">{threadLabel(threads.find(thread => thread.id === threadId) ?? { id: threadId })}</span>}{!settingsOpen && <div className="workspace-actions"><button aria-pressed={panel === "changes"} className={panel === "changes" ? "selected" : ""} disabled={reviewBusy || !workspace} onClick={() => setPanel(panel === "changes" ? null : "changes")}>Changes{turnDiff ? " •" : ""}</button><button aria-pressed={panel === "terminal"} className={panel === "terminal" ? "selected" : ""} disabled={reviewBusy || goalWorking} onClick={() => setPanel(panel === "terminal" ? null : "terminal")}>Terminal{terminalActive ? " •" : ""}</button></div>}</header>
      {built === false && <div className="native-warning">Native runtime is missing. Build scripts/build-smara-native.ps1; no old-engine fallback is used.</div>}
      {error && <div role="alert" className="native-error">{error}{!settingsOpen && !connected && (!busy || uncertain) && <button disabled={connecting || loadingThread} onClick={() => void recoverSession()}>Retry setup</button>}<button aria-label="Dismiss error" onClick={() => setError("")}>×</button></div>}
      {settingsOpen && setup ? <NativeSettings setup={setup} locked={connected || connecting} onUpdate={updateSetup} onClose={() => { if (!settingsPendingRef.current) setSettingsOpen(false); }} onPendingChange={pending => { settingsPendingRef.current = pending; setSettingsSaving(pending); }} version={packageInfo.version} runtimeBuilt={built === true} toolsEnabled={toolsEnabled} workersEnabled={workersEnabled} browserOrigins={browserOrigins} onToolsChange={setToolsEnabled} onWorkersChange={setWorkersEnabled} onOriginsChange={setBrowserOrigins} /> : <>
      <div className={`native-work-area ${panel ? "with-panel" : ""}`}>
      <div className="native-chat-area">
      {goalInAnotherConversation && <div className="goal-other-chat" role="status"><span><strong>Background goal {goalLabel(currentGoal!.state).toLowerCase()}</strong><small>Chat and project activity are paused while it works.</small></span><button disabled={connecting || loadingThread} onClick={() => void openGoalConversation()}>Open task chat</button></div>}
      {goalInThisConversation && currentGoal && <section className={`native-goal-status state-${currentGoal.state}`} aria-label="Autonomous goal progress">
        <div className="native-goal-heading"><div><span className="goal-live-dot" /><strong>{currentGoal.spec?.origin_thread_id ? goalLabel(currentGoal.state) : `Project goal · ${goalLabel(currentGoal.state)}`}</strong><small>Cycle {currentGoal.cycles || 0}/{currentGoal.spec?.max_cycles ?? 0} · {currentGoal.requests || 0}/{currentGoal.spec?.max_requests ?? 0} requests{currentGoal.spec?.max_token_estimate ? ` · ${Math.round((currentGoal.token_estimate || 0) / 1000)}k/${Math.round((currentGoal.spec.max_token_estimate) / 1000)}k tokens` : ""}</small></div><div className="goal-heading-aside"><small>{currentGoal.updated ? `Updated ${Math.max(0, Math.floor((activityClock - currentGoal.updated * 1000) / 1000))}s ago` : "Saved"}</small>{!currentGoal.lease && <button className="goal-dismiss-btn" disabled={goalPending} title="Dismiss goal" aria-label="Dismiss goal" onClick={() => void dismissGoal(currentGoal.id)}>✕</button>}</div></div>
        <p className="goal-objective" title={currentGoal.spec?.objective}>{currentGoal.spec?.objective}</p>
        <div className="goal-progress-line"><span role="status">{currentGoal.detail || (currentGoal.state === "queued" ? "Waiting for the background worker" : goalLabel(currentGoal.state))}</span>
          {goalServiceStarting && <small>Starting background worker…</small>}
          {!goalServiceRunning && !goalServiceStarting && !["completed", "stopped"].includes(currentGoal.state) && <small className="goal-service-offline">Background worker offline</small>}
          {goalServiceError && <small className="goal-service-offline">{goalServiceError}</small>}
          {goalPollError && <small className="goal-service-offline">{goalPollError}</small>}
        </div>
        {!!currentGoal.events?.length && <details className="goal-timeline"><summary>Activity · {currentGoal.events.length} updates</summary><ol>{currentGoal.events.slice(-5).reverse().map(event => <li key={event.seq}><span>{event.body.summary || event.kind}</span><time>{new Date(event.created * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</time></li>)}</ol></details>}
        {!!currentGoal.checkpoint?.summary && currentGoal.state === "ready_for_review" && <NativeMarkdown text={currentGoal.checkpoint.summary} onPreviewFile={setPreviewPath} />}
        {currentGoal.approvals?.map(request => {
          const questions = request.params.questions as Question[] | undefined;
          const isQuestion = request.method === "item/tool/requestUserInput" && Array.isArray(questions);
          const canDeny = supportsApproval(request.method, request.params) || request.method === "mcpServer/elicitation/request";
          return <div className="native-goal-approval" key={request.id}>
            <strong>{isQuestion ? "Smara needs your input" : `Smara needs your approval · ${request.method}`}</strong>
            {isQuestion ? <>
              {questions!.map(question => <label key={question.id} className="goal-question">{question.question}
                {question.options?.map(option => <button type="button" key={option.label} title={option.description}
                  onClick={() => setAnswers(current => ({ ...current, [answerKey(request.id, question.id)]: option.label }))}>{option.label}</button>)}
                <input type={question.isSecret ? "password" : "text"} value={answers[answerKey(request.id, question.id)] ?? ""}
                  onChange={event => setAnswers(current => ({ ...current, [answerKey(request.id, question.id)]: event.target.value }))} />
              </label>)}
              <button disabled={goalPending} onClick={() => void answerGoalQuestions(currentGoal.id, request.id, questions!)}>Continue</button>
            </> : <>
              <pre>{JSON.stringify(request.params, null, 2)}</pre>
              {supportsApproval(request.method, request.params) && <button disabled={goalPending}
                onClick={() => void goalAction("respond", { id: currentGoal.id, request_id: request.id, allow: true })}>Allow this action</button>}
              {canDeny && <button disabled={goalPending} onClick={() => void goalAction("respond", { id: currentGoal.id, request_id: request.id, allow: false })}>Deny</button>}
              {!canDeny && <small>Pause the goal and add guidance below to handle this specialized request safely.</small>}
            </>}
          </div>;
        })}
        <div className="native-goal-actions">
          {currentGoal.state === "ready_for_review" && <button disabled={goalPending} onClick={() => void goalAction("accept", { id: currentGoal.id })}>Accept result</button>}
          {!["completed", "stopped"].includes(currentGoal.state) && !goalServiceRunning && !goalServiceStarting && <button disabled={goalPending} onClick={() => void goalAction("start_service")}>Retry worker</button>}
          {["queued", "running", "awaiting_approval"].includes(currentGoal.state) && <button disabled={goalPending} onClick={() => void goalAction("pause", { id: currentGoal.id })}>Pause</button>}
          {["paused", "blocked"].includes(currentGoal.state) && ((currentGoal.detail || "").toLowerCase().includes("budget") || (currentGoal.token_estimate >= (currentGoal.spec?.max_token_estimate || 500_000))) ? (
            <button disabled={goalPending || !!currentGoal.lease} onClick={() => void extendAndResumeGoal(currentGoal)}>Extend budget & Resume</button>
          ) : ["paused", "blocked"].includes(currentGoal.state) ? (
            <button disabled={goalPending || !!currentGoal.lease} onClick={() => void goalAction("resume", { id: currentGoal.id })}>Resume</button>
          ) : null}
          {!["completed", "stopped"].includes(currentGoal.state) && <button disabled={goalPending} onClick={() => void goalAction("stop", { id: currentGoal.id })}>Stop</button>}
        </div>
        {!["completed", "stopped"].includes(currentGoal.state) && <details className="goal-guidance"><summary>Guide this task</summary><form onSubmit={event => { event.preventDefault(); void submitGoalFeedback(currentGoal.id); }}><textarea aria-label="Goal feedback" value={goalFeedback} onChange={event => setGoalFeedback(event.target.value)} placeholder="Correct the direction, answer a question, or add missing requirements…" /><button disabled={goalPending || !goalFeedback.trim()}>Send guidance</button></form></details>}
      </section>}
      {uncertain && <div className="native-warning"><button onClick={() => void checkStatus()}>Check response status</button><button disabled={connecting || loadingThread} onClick={() => void recoverSession()}>Recover chat</button> The request may have been accepted. Your message will not be resent automatically.</div>}
      <div className="native-transcript" aria-live="off">
        {busy && turnStartedAt && <p role="status">Working for {Math.max(0, Math.floor((activityClock - turnStartedAt) / 60000))}m {Math.max(0, Math.floor((activityClock - turnStartedAt) / 1000) % 60)}s · Stop is available below.</p>}
        {!!workers.length && <section className="native-workers"><strong>Workers · {workers.filter(worker => worker.status === "completed").length}/{workers.length} complete · changes are not auto-merged</strong>{workers.map(worker => <div key={worker.id}><code>{worker.id}</code> · {worker.status}{worker.activity && <span> · {worker.activity}{worker.lastActivityAt && ` · ${Math.max(0, Math.floor((activityClock - worker.lastActivityAt) / 1000))}s ago`}</span>} <button disabled={!connected || connecting || loadingThread} onClick={() => void inspectWorker(worker.id)}>Check status & checkout</button>{worker.cwd && <pre>{worker.cwd}</pre>}</div>)}</section>}
        {!items.length && <div className="native-empty"><h1>What can I help you with?</h1><p>Research, build, and get work done.</p><div className="starter-cards">{[{ title: "Understand this project", text: "Inspect this project and explain its architecture, entry points and how to run its tests. Do not change files." }, { title: "Find & fix a bug", text: "Inspect this project for a reproducible bug. Show evidence, make a minimal fix, and run the relevant tests." }, { title: "Research a topic", text: "Research a topic using public sources. Read the actual clock for time-sensitive questions, verify claims against fetched pages and cite them. Ask me for the topic first." }].map(card => <button key={card.title} title={card.text} onClick={() => setDraft(card.text)}>{card.title}<span>↗</span></button>)}</div>{!profile && <small className="empty-hint">Add your model in Settings to get started.</small>}</div>}
        {items.map(item => <article key={item.id} className={`native-item ${item.kind}`}>
          <div className="item-heading"><small>{item.kind === "assistant" ? "Smara" : item.kind === "user" ? "You" : item.kind} {item.status && `· ${item.status}`}</small><div>
            {item.kind === "assistant" && <ReadAloud text={item.text} disabled={busy} />}
            {(item.kind === "assistant" || item.kind === "user") && <button className="text-button" title={item.kind === "assistant" ? "Copy response" : "Copy your message"} aria-label={item.kind === "assistant" ? "Copy response" : "Copy your message"} onClick={() => void copyMessage(item)}>{copiedItemId === item.id ? "✓ Copied" : item.kind === "assistant" ? "Copy response" : "Copy"}</button>}
            {item.kind === "user" && !busy && <button className="text-button" title="Load this text into the composer; sending creates a new follow-up" onClick={() => editMessage(item.text)}>Edit</button>}
          </div></div>
          {item.kind === "assistant" ? <NativeMarkdown text={item.text} onPreviewFile={setPreviewPath} /> : item.kind === "user" ? <pre>{item.text}</pre> : item.kind === "commandExecution" ? <NativeCommand item={item} /> : <details open={item.status === "inProgress" || item.status === "failed"}><summary>Tool details · {item.text.length.toLocaleString()} characters</summary><pre>{item.text}</pre></details>}
        </article>)}
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
      <footer><form className="native-composer" onSubmit={event => { event.preventDefault(); void send(); }}>
        {!!contextFiles.length && <div className="composer-files">{contextFiles.map(file => <span key={file.path}><button type="button" title={file.path} onClick={() => setPreviewPath(file.path)}>{file.name}</button><button type="button" aria-label={`Remove ${file.name}`} disabled={navigationLocked} onClick={() => setContextFiles(current => current.filter(item => item.path !== file.path))}>×</button></span>)}</div>}
        {editingPrompt && <div className="composer-edit-notice">Sending this edited text adds a follow-up; the original stays in chat history. <button type="button" onClick={() => { setEditingPrompt(false); setDraft(""); }}>Cancel edit</button></div>}
        {slashOptions.length > 0 && <div className="slash-command-menu" role="listbox" aria-label="Smara commands">{slashOptions.map((option, index) => <button type="button" role="option" aria-selected={index === slashSelection} key={option.command} onMouseDown={event => event.preventDefault()} onMouseEnter={() => setSlashSelection(index)} onClick={() => chooseSlashCommand(option.command)}><code>{option.command}</code><span><strong>{option.label}</strong><small>{option.description}</small></span><kbd>↵</kbd></button>)}</div>}
        <textarea ref={composerRef} aria-label="Message Smara" rows={2} placeholder="Ask Smara anything… · type / for commands" value={draft} onChange={event => { setDraft(event.target.value); setSlashSelection(0); event.target.style.height = "auto"; event.target.style.height = `${Math.min(event.target.scrollHeight, 260)}px`; }} onKeyDown={event => {
          if (slashOptions.length && !event.shiftKey && !event.nativeEvent.isComposing) {
            if (event.key === "ArrowDown") { event.preventDefault(); setSlashSelection(value => (value + 1) % slashOptions.length); return; }
            if (event.key === "ArrowUp") { event.preventDefault(); setSlashSelection(value => (value + slashOptions.length - 1) % slashOptions.length); return; }
            if (event.key === "Escape") { event.preventDefault(); setDraft(""); return; }
            if (event.key === "Enter") { event.preventDefault(); chooseSlashCommand(slashOptions[slashSelection]?.command ?? slashOptions[0].command); return; }
          }
          if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void send(); }
        }} />
        <div className="composer-toolbar"><div className="composer-context"><button type="button" aria-label="Add project files" title="Add project files" disabled={navigationLocked || !workspace} onClick={() => setContextOpen(true)}>＋</button><NativeVoice key={workspace + threadId} disabled={navigationLocked} onTranscript={text => setDraft(current => current ? current + "\n" + text : text)} /><small>{connecting ? "Preparing project…" : ""}</small></div><div className="composer-actions">
          {canContinueTurn && !busy && <button type="button" className="continue-button" disabled={goalWorking || connecting || loadingThread} title="Start a fresh turn in this conversation; previous tools will not replay" onClick={() => void send("Continue from where you left off. First inspect the current state and completed tool results so you do not repeat actions.")}>Continue</button>}
          <select aria-label="Model" title="Choose a model" value={profile} disabled={navigationLocked} onChange={event => void selectModel(event.target.value)}><option value="" disabled>Choose model</option>{profiles.map(model => <option value={model.id} key={model.id}>{model.label}</option>)}</select>
          <button className={busy ? "stop-button" : "primary"} aria-label={busy ? "Stop response" : "Send message"} title={busy ? "Stop response" : "Send message"} type={busy ? "button" : "submit"} disabled={reviewBusy || connecting || loadingThread || goalPending || (busy ? !turnId : (goalWorking && !/^\/goal(?:\s|$)/i.test(draft.trim())) || !profile || built !== true || !draft.trim() || approvals.length > 0)} onClick={busy ? () => void stopTurn() : undefined}>{busy ? "■" : "↑"}</button>
        </div></div>
      </form><div className="composer-status"><small role="status">{reviewBusy ? "Saving local review…" : busy || uncertain || loadingThread || (status !== "Ready" && status !== "completed") ? status : ""}</small><small>Shift+Enter for a new line</small></div></footer>
      </div>
      {panel === "changes" && <NativeChanges key={workspace + threadId} workspace={workspace} threadId={threadId} locked={navigationLocked || terminalActive || goalWorking} revision={reviewRevision} notice={reviewNotice} onBusyChange={reviewPending} onPreviewFile={setPreviewPath} onClose={() => setPanel(null)} />}
      {panel === "terminal" && <NativeTerminal workspace={workspace} connected={connected} session={terminalSession} interactiveSupported={terminalPlatformSupported} onConnect={() => { void connect(); }} onStart={() => startTerminal()} onInput={value => { void writeTerminal(value); }} onInterrupt={interruptTerminal} onStop={() => { void stopTerminal(); }} onResize={resizeTerminal} onClear={() => setTerminalSession(current => current ? { ...current, output: "", truncated: false } : current)} onClose={() => setPanel(null)} />}
      </div>
      </>}
    </main></div>
    {previewPath && <NativeFilePreview workspace={workspace} path={previewPath} onClose={() => setPreviewPath(null)} />}
    {contextOpen && <NativeContextPicker workspace={workspace} files={contextFiles} onAttach={setContextFiles} onClose={() => setContextOpen(false)} />}
  </div>;
}
