// Synthetic React-hook/transport harness. Installed GUI acceptance is separate.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";

function fixture({ startupFailure = false, unknownTurn = false, sandboxReady = true, terminalPlatformSupported = true, projectOptions, backgroundGoal = false } = {}) {
  const slots = [], calls = [], goalCalls = [], effects = [], timers = new Set();
  let cursor = 0, dirty = true, tree, failResume = false, currentRpc;
  const setup = {
    preferences: { workspace: "C:/first", active_model: "one", projects: [{ workspace: "C:/first", label: "First" }, { workspace: "C:/second", label: "Second" }], search_provider: "tavily" },
    profiles: [{ id: "one", label: "Offline one", model: "offline-one" }, { id: "two", label: "Offline two", model: "offline-two" }], credentials: [], search: { configured: false },
  };
  if (projectOptions) setup.preferences.projects[0].options = projectOptions;
  const goalRecord = backgroundGoal && typeof backgroundGoal === "object" ? backgroundGoal : backgroundGoal ? {
    id: "fixturegoal", state: "running", lease: "synthetic-active-goal", updated: Date.now() / 1000, cycles: 1, requests: 1, token_estimate: 100,
    detail: "Synthetic task is working", session_id: "synthetic-goal-session", spec: { objective: "Synthetic goal", success_criteria: "Evidence", workspace: "C:/first", profile_id: "one", sandbox: "read-only", max_cycles: 8, max_requests: 30, max_token_estimate: 500000 },
    checkpoint: {}, events: [], approvals: [],
  } : null;
  const hooks = {
    useState(initial) {
      const index = cursor++;
      if (!(index in slots)) slots[index] = typeof initial === "function" ? initial() : initial;
      return [slots[index], value => { const next = typeof value === "function" ? value(slots[index]) : value; if (!Object.is(next, slots[index])) { slots[index] = next; dirty = true; } }];
    },
    useRef(initial) { const index = cursor++; return slots[index] ??= { current: initial }; },
    useEffect(effect, deps) {
      const index = cursor++, previous = slots[index];
      if (!previous || !deps || deps.some((value, i) => !Object.is(value, previous.deps[i]))) {
        slots[index] = { deps, cleanup: previous?.cleanup };
        effects.push(() => { previous?.cleanup?.(); slots[index].cleanup = effect(); });
      }
    },
  };
  const jsx = (type, props) => ({ type, props: props ?? {} });
  const evaluate = (path, require) => {
    const module = { exports: {} };
    const source = readFileSync(new URL(path, import.meta.url), "utf8");
    const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true } }).outputText;
    vm.runInNewContext(code, { module, exports: module.exports, require, setTimeout: callback => { timers.add(callback); return callback; }, clearTimeout: timer => timers.delete(timer), setInterval: callback => { timers.add(callback); return callback; }, clearInterval: timer => timers.delete(timer), crypto: { randomUUID: () => "fixture-terminal-id" }, TextEncoder, TextDecoder, Uint8Array, btoa, atob, window: { addEventListener() {}, removeEventListener() {} }, navigator: { clipboard: { writeText: async () => {} } } });
    return module.exports;
  };
  class RpcOutcomeUnknown extends Error {}
  class NativeRpc {
    generation = "";
    constructor(receive) { this.receive = receive; currentRpc = this; }
    async connect(workspace, profile, readers, workers, origins) {
      calls.push({ method: "connect", workspace, profile, readers, workers, origins: [...origins] });
      if (startupFailure) { startupFailure = false; throw new Error("Synthetic startup failure"); }
      this.generation = String(calls.length);
    }
    async stop() { calls.push({ method: "stop" }); this.generation = ""; }
    async request(method, params) {
      calls.push({ method, params: JSON.parse(JSON.stringify(params)) });
      const thread = { id: "saved", cwd: failResume ? "C:/wrong-project" : setup.preferences.workspace, turns: [{ id: "turn", status: "interrupted", items: [{ id: "user", type: "userMessage", content: [{ type: "text", text: "Synthetic saved question" }] }] }] };
      if (method === "thread/list") return { data: [{ id: "saved", preview: "Saved chat" }] };
      if (method === "thread/read" || method === "thread/resume") return { thread };
      if (method === "thread/start") return { thread: { id: "new" } };
      if (method === "windowsSandbox/readiness") return { status: sandboxReady ? "ready" : "notConfigured" };
      if (method === "command/exec") return new Promise(() => {});
      if (method === "turn/start") {
        if (unknownTurn) throw new RpcOutcomeUnknown("Synthetic unknown outcome");
        this.receive({ method: "turn/started", params: { threadId: params.threadId, turn: { id: "running" } } });
      }
      return {};
    }
  }
  const api = {
    sameWorkspace: (a, b) => a.toLowerCase() === b.toLowerCase(),
    nativeSettings: async (operation, args) => {
      if (operation === "select_model") setup.preferences.active_model = args.id;
      if (operation === "select_project") setup.preferences.workspace = args.workspace;
      return { ...setup, preferences: { ...setup.preferences } };
    },
  };
  const view = evaluate("../src/nativeView.ts", () => assert.fail("View must have no side-effect imports"));
  const context = evaluate("../src/nativeContext.ts", () => assert.fail("Context formatting must have no side-effect imports"));
  const terminalProtocol = evaluate("../src/terminalProtocol.ts", () => assert.fail("Terminal protocol must have no side-effect imports"));
  const goal = evaluate("../src/goalApi.ts", n => n === "@tauri-apps/api/core" ? { invoke: async () => ({}) } : assert.fail(n));
  function Settings() {}
  function Changes() {}
  function Terminal() {}
  function Command() {}
  function ContextPicker() {}
  const App = evaluate("../src/NativeApp.tsx", name => {
    if (name === "react") return hooks;
    if (name === "react/jsx-runtime") return { jsx, jsxs: jsx };
    if (name === "@tauri-apps/api/core") return { invoke: async () => ({ built: true, windowsTerminalBuild: terminalPlatformSupported }) };
    if (name.endsWith("NativeSettings")) return { default: Settings, __esModule: true };
    if (name.endsWith("NativeGoals")) return { default: () => null, __esModule: true };
    if (name.endsWith("NativeVoice")) return { default: () => null, ReadAloud: () => null, __esModule: true };
    if (name.endsWith("goalApi")) return { ...goal, goalRequest: async (action, values = {}) => {
      goalCalls.push({ action, values: JSON.parse(JSON.stringify(values)) });
      if (action === "list") return { goals: goalRecord ? [goalRecord] : [], current_goal: goalRecord, service: { running: !!goalRecord } };
      return { id: values.id ?? goalRecord?.id };
    } };
    if (name.endsWith("NativeMarkdown")) return { default: () => null, __esModule: true };
    if (name.endsWith("NativeFilePreview")) return { default: () => null, __esModule: true };
    if (name.endsWith("NativeContextPicker")) return { default: ContextPicker, __esModule: true };
    if (name.endsWith("nativeContext")) return context;
    if (name.endsWith("NativeTerminal")) return { default: Terminal, __esModule: true };
    if (name.endsWith("NativeChanges")) return { default: Changes, __esModule: true, reviewRequest: async (workspace, operation, values) => { calls.push({ method: `review/${operation}`, workspace, values }); return operation === "capture" ? { snapshot: "synthetic-local-snapshot", excluded: 0 } : {}; } };
    if (name.endsWith("NativeCommand")) return { default: Command, __esModule: true };
    if (name.endsWith("nativeApi")) return api;
    if (name.endsWith("nativeRpc")) return { NativeRpc, RpcOutcomeUnknown };
    if (name.endsWith("nativeView")) return view;
    if (name.endsWith("terminalProtocol")) return terminalProtocol;
    if (name.endsWith("package.json")) return { version: "fixture" };
    if (name.endsWith(".css")) return {};
    assert.fail(`Unexpected import ${name}`);
  }).default;
  const nodes = () => {
    const result = [];
    const visit = node => { if (Array.isArray(node)) node.forEach(visit); else if (node && typeof node === "object") { result.push(node); visit(node.props?.children); } };
    visit(tree); return result;
  };
  const text = node => Array.isArray(node) ? node.map(text).join("") : node && typeof node === "object" ? text(node.props?.children) : String(node ?? "");
  const button = label => { const found = nodes().find(node => node.type === "button" && (text(node) === label || node.props["aria-label"] === label)); assert.ok(found, `Missing button ${label}`); return found; };
  const flush = async () => {
    for (let i = 0; i < 30; i++) {
      if (dirty) { dirty = false; cursor = 0; tree = App(); while (effects.length) effects.shift()(); }
      await Promise.resolve();
    }
    assert.equal(dirty, false, "State failed to settle");
  };
  const enter = async message => {
    nodes().find(node => node.type === "textarea").props.onChange({ target: { value: message, style: {}, scrollHeight: 100 } });
    await flush();
  };
  return { calls, goalCalls, flush, button, nodes, enter, contextPicker: () => nodes().find(node => node.type === ContextPicker)?.props, emit: message => currentRpc.receive(message), terminal: () => nodes().find(node => node.type === Terminal)?.props, changes: () => nodes().find(node => node.type === Changes)?.props, failResume: () => { failResume = true; }, settings: () => nodes().find(node => node.type === Settings).props, model: () => nodes().find(node => node.type === "select" && node.props["aria-label"] === "Model") };
}

test("turn snapshots are local, captured before acceptance and sealed after completion", async () => {
  const f = fixture(); await f.flush(); await f.enter("Synthetic coding task");
  f.button("Send message").props.onClick?.();
  f.nodes().find(node => node.type === "form" && node.props.className === "native-composer").props.onSubmit({ preventDefault() {} });
  await f.flush();
  assert.ok(f.calls.findIndex(call => call.method === "review/capture") < f.calls.findIndex(call => call.method === "turn/start"));
  f.emit({ method: "turn/completed", params: { threadId: "new", turn: { id: "running", status: "completed" } } });
  await f.flush();
  assert.equal(f.calls.filter(call => call.method === "review/seal").length, 1);
  assert.deepEqual(f.calls.find(call => call.method === "turn/start").params.input, [{ type: "text", text: "Synthetic coding task", text_elements: [] }]);
});

test("an active background goal blocks a new foreground turn", async () => {
  const f = fixture({ backgroundGoal: true }); await f.flush(); await f.enter("Another task in the same project");
  assert.equal(f.button("Send message").props.disabled, true);
  f.nodes().find(node => node.type === "form" && node.props.className === "native-composer").props.onSubmit({ preventDefault() {} });
  await f.flush();
  assert.equal(f.calls.some(call => call.method === "turn/start"), false);
});

test("an active goal cannot be dismissed from chat while it owns its worker lease", async () => {
  const f = fixture({ backgroundGoal: true }); await f.flush();
  assert.equal(f.nodes().some(node => node.props?.["aria-label"] === "Dismiss goal"), false);
  assert.equal(f.button("Send message").props.disabled, true);
  assert.equal(f.goalCalls.some(call => call.action === "delete"), false);
});

test("autonomous goals can answer native user-input requests in the same chat", async () => {
  const f = fixture({ backgroundGoal: {
    id: "fixturegoal", state: "awaiting_approval", lease: "synthetic-active-goal", updated: Date.now() / 1000, cycles: 1, requests: 1, token_estimate: 100,
    detail: "Waiting for your response", session_id: "synthetic-goal-session", spec: { objective: "Synthetic goal", success_criteria: "Evidence", workspace: "C:/first", profile_id: "one", sandbox: "read-only", max_cycles: 8, max_requests: 30, max_token_estimate: 500000 },
    checkpoint: {}, events: [], approvals: [{ id: "question-receipt", method: "item/tool/requestUserInput", params: { questions: [{ id: "choice", question: "Choose the synthetic mode", options: [{ label: "safe", description: "Use safe mode" }] }] } }],
  } });
  await f.flush();
  const input = f.nodes().find(node => node.type === "input" && node.props.type === "text");
  assert.ok(input);
  input.props.onChange({ target: { value: "safe" } }); await f.flush();
  f.button("Continue").props.onClick(); await f.flush();
  const response = f.goalCalls.find(call => call.action === "respond");
  assert.deepEqual(response.values, { id: "fixturegoal", request_id: "question-receipt", answers: { choice: { answers: ["safe"] } } });
});

test("coding goals get workspace-scoped write access while research goals stay read-only", async () => {
  for (const [prompt, expectedSandbox] of [["/goal fix the bug in this repository", "workspace-write"], ["/goal research the latest market size", "read-only"]]) {
    const f = fixture(); await f.flush(); await f.enter(prompt);
    f.nodes().find(node => node.type === "form" && node.props.className === "native-composer").props.onSubmit({ preventDefault() {} });
    await f.flush();
    assert.equal(f.goalCalls.find(call => call.action === "create")?.values.sandbox, expectedSandbox);
  }
});

test("Changes is readable during work but writes and navigation remain locked", async () => {
  const f = fixture(); await f.flush(); await f.enter("Synthetic task");
  f.nodes().find(node => node.type === "form" && node.props.className === "native-composer").props.onSubmit({ preventDefault() {} });
  await f.flush(); f.button("Changes").props.onClick(); await f.flush();
  assert.equal(f.changes().locked, true);
  assert.equal(f.button("＋ New chat").props.disabled, true);
  f.emit({ method: "turn/diff/updated", params: { threadId: "new", diff: "synthetic diff" } });
  await f.flush(); assert.ok(f.button("Changes •"));
});

test("a user Git action synchronously blocks sending or changing project", async () => {
  const f = fixture(); await f.flush(); f.button("Changes").props.onClick(); await f.flush();
  f.changes().onBusyChange(true);
  await f.enter("Keep this draft");
  f.nodes().find(node => node.type === "form" && node.props.className === "native-composer").props.onSubmit({ preventDefault() {} });
  await f.flush();
  assert.equal(f.calls.some(call => call.method === "turn/start"), false);
  assert.equal(f.button("＋ New chat").props.disabled, true);
  assert.equal(f.model().props.disabled, true);
  assert.equal(f.nodes().find(node => node.type === "textarea").props.value, "Keep this draft");
  f.changes().onBusyChange(false); await f.flush();
  assert.equal(f.button("Send message").props.disabled, false);
});

test("startup prepares a project without sending a turn or enabling optional tools", async () => {
  const f = fixture(); await f.flush();
  const connect = f.calls.find(call => call.method === "connect");
  assert.equal(connect.profile, "one");
  assert.equal(connect.readers, false); assert.equal(connect.workers, false); assert.equal(connect.origins.length, 0);
  assert.equal(f.calls.filter(call => call.method === "connect").length, 1);
  assert.equal(f.calls.some(call => call.method === "turn/start"), false);
  assert.equal(f.nodes().some(node => node.type === "button" && ["Connect", "Disconnect", "Manage settings"].includes(node.props.children)), false);
  assert.ok(f.model());
  assert.ok(f.button("Add project files"));
});

test("saved project choices are restored without leaking to another project", async () => {
  const f = fixture({ projectOptions: { tools_enabled: true, workers_enabled: true, browser_origins: ["https://docs.python.org"] } });
  await f.flush();
  const first = f.calls.find(call => call.method === "connect");
  assert.equal(first.readers, true); assert.equal(first.workers, true);
  assert.deepEqual(first.origins, ["https://docs.python.org"]);
  await f.nodes().find(node => node.type === "button" && node.props.title === "C:/second").props.onClick(); await f.flush();
  assert.equal(f.calls.filter(call => call.method === "connect").at(-1).workers, false);
  await f.nodes().find(node => node.type === "button" && node.props.title === "C:/first").props.onClick(); await f.flush();
  assert.equal(f.calls.filter(call => call.method === "connect").at(-1).workers, true);
});

test("project files send references, not eager file contents, and clear only after acceptance", async () => {
  const f = fixture(); await f.flush();
  f.button("Add project files").props.onClick(); await f.flush();
  f.contextPicker().onAttach([{ name: "report.pdf", path: "reports/report.pdf", directory: false, size: 42 }]);
  f.contextPicker().onClose(); await f.flush();
  await f.enter("Summarize this file");
  f.nodes().find(node => node.type === "form" && node.props.className === "native-composer").props.onSubmit({ preventDefault() {} });
  await f.flush();
  const input = f.calls.find(call => call.method === "turn/start").params.input;
  assert.equal(input.length, 1); assert.equal(input[0].type, "text");
  assert.match(input[0].text, /Summarize this file/);
  assert.match(input[0].text, /"reports\/report.pdf"/);
  assert.match(input[0].text, /ask before uploading/);
  assert.equal(f.nodes().some(node => node.props?.["aria-label"] === "Remove report.pdf"), false);
});

test("uncertain send preserves file context and project switches preserve separate drafts", async () => {
  const f = fixture({ unknownTurn: true }); await f.flush();
  f.button("Add project files").props.onClick(); await f.flush();
  f.contextPicker().onAttach([{ name: "notes.txt", path: "notes.txt", directory: false, size: 4 }]);
  f.contextPicker().onClose(); await f.flush();
  await f.nodes().find(node => node.type === "button" && node.props.title === "C:/second").props.onClick(); await f.flush();
  assert.equal(f.nodes().some(node => node.props?.["aria-label"] === "Remove notes.txt"), false);
  await f.nodes().find(node => node.type === "button" && node.props.title === "C:/first").props.onClick(); await f.flush();
  assert.ok(f.button("Remove notes.txt"));
  await f.enter("Read notes");
  f.nodes().find(node => node.type === "form" && node.props.className === "native-composer").props.onSubmit({ preventDefault() {} });
  await f.flush();
  assert.ok(f.button("Remove notes.txt"));
  assert.equal(f.button("Remove notes.txt").props.disabled, true);
  assert.equal(f.calls.filter(call => call.method === "turn/start").length, 1);
});

test("the primary UI has no technical badge, tool dropdown or capabilities tab", () => {
  const app = readFileSync(new URL("../src/NativeApp.tsx", import.meta.url), "utf8");
  const settings = readFileSync(new URL("../src/NativeSettings.tsx", import.meta.url), "utf8");
  assert.doesNotMatch(app, /sandbox-badge|toolsOpen|Manage settings|native-connection/);
  assert.doesNotMatch(settings, /tab === "capabilities"|"Capabilities"/);
  assert.match(settings, /action approvals remain required/);
});

test("setup help documents scope and gates provider checks behind confirmation", () => {
  const settings = readFileSync(new URL("../src/NativeSettings.tsx", import.meta.url), "utf8");
  assert.match(settings, /Help & setup/);
  assert.match(settings, /window\.confirm\(explanation\)/);
  assert.match(settings, /nativeProbe\(operation/);
  assert.match(settings, /fixed minimal requests/);
  assert.match(settings, /Full desktop\/computer control and unattended company operations are not enabled/);
});

test("settings pause the idle runtime and restore the same chat without replay", async () => {
  const f = fixture(); await f.flush();
  await f.button("Saved chat").props.onClick(); await f.flush();
  await f.button("⚙ Settings").props.onClick(); await f.flush();
  assert.equal(f.settings().locked, false);
  f.settings().onClose(); await f.flush();
  assert.equal(f.calls.filter(call => call.method === "thread/resume").length, 2);
  assert.equal(f.calls.some(call => call.method === "turn/start"), false);
});

test("composer model selection restarts the idle context and preserves history and draft", async () => {
  const f = fixture(); await f.flush();
  await f.button("Saved chat").props.onClick(); await f.flush();
  await f.enter("My unsubmitted draft");
  await f.model().props.onChange({ target: { value: "two" } }); await f.flush();
  assert.equal(f.calls.filter(call => call.method === "connect").at(-1).profile, "two");
  assert.equal(f.calls.filter(call => call.method === "thread/resume").length, 2);
  assert.equal(f.calls.filter(call => call.method === "thread/resume").at(-1).params.model, "offline-two");
  assert.equal(f.calls.filter(call => call.method === "thread/resume").at(-1).params.modelProvider, "smara_chat_adapter");
  assert.equal(f.nodes().find(node => node.type === "textarea").props.value, "My unsubmitted draft");
  assert.equal(f.calls.some(call => call.method === "turn/start"), false);
});

test("leaving settings waits for configuration writes instead of starting stale setup", async () => {
  const f = fixture(); await f.flush();
  await f.button("⚙ Settings").props.onClick(); await f.flush();
  f.settings().onPendingChange(true); await f.flush();
  assert.equal(f.button("＋ New chat").props.disabled, true);
  f.settings().onClose(); f.button("＋ New chat").props.onClick(); await f.flush();
  assert.ok(f.settings());
  assert.equal(f.calls.filter(call => call.method === "connect").length, 1);
  f.settings().onPendingChange(false); await f.flush();
  f.settings().onClose(); await f.flush();
  assert.equal(f.calls.filter(call => call.method === "connect").length, 2);
});

test("project changes never restore a different project's thread or carry optional permissions", async () => {
  const f = fixture(); await f.flush();
  await f.button("Saved chat").props.onClick(); await f.flush();
  await f.button("⚙ Settings").props.onClick(); await f.flush();
  f.settings().onToolsChange(true); f.settings().onWorkersChange(true); f.settings().onOriginsChange("https://example.com"); await f.flush();
  const project = f.nodes().find(node => node.type === "button" && node.props.title === "C:/second");
  await project.props.onClick(); await f.flush();
  const last = f.calls.filter(call => call.method === "connect").at(-1);
  assert.equal(last.workspace, "C:/second"); assert.equal(last.readers, false); assert.equal(last.workers, false); assert.equal(last.origins.length, 0);
  assert.equal(f.calls.filter(call => call.method === "thread/resume").length, 1);
});

test("startup failure settles without an automatic retry loop; recovery is explicit", async () => {
  const f = fixture({ startupFailure: true }); await f.flush(); await f.flush();
  assert.equal(f.calls.filter(call => call.method === "connect").length, 1);
  await f.button("Retry setup").props.onClick(); await f.flush();
  assert.equal(f.calls.filter(call => call.method === "connect").length, 2);
  assert.equal(f.calls.some(call => call.method === "turn/start"), false);
});

test("an unknown turn remains locked and cannot be automatically resent", async () => {
  const f = fixture({ unknownTurn: true }); await f.flush(); await f.enter("Do not repeat this");
  const submit = f.nodes().find(node => node.type === "form" && node.props.className === "native-composer").props.onSubmit;
  submit({ preventDefault() {} }); submit({ preventDefault() {} }); await f.flush();
  assert.equal(f.calls.filter(call => call.method === "turn/start").length, 1);
  assert.ok(f.button("Recover chat"));
  assert.equal(f.model().props.disabled, true);
  assert.equal(f.nodes().find(node => node.type === "textarea").props.value, "Do not repeat this");
});

test("failed history restoration cannot silently send a reply into a new chat", async () => {
  const f = fixture(); await f.flush();
  await f.button("Saved chat").props.onClick(); await f.flush();
  await f.button("⚙ Settings").props.onClick(); await f.flush();
  f.failResume(); f.settings().onClose(); await f.flush(); await f.enter("Reply to saved history");
  f.nodes().find(node => node.type === "form" && node.props.className === "native-composer").props.onSubmit({ preventDefault() {} }); await f.flush();
  assert.equal(f.calls.some(call => call.method === "thread/start" || call.method === "turn/start"), false);
});

test("interactive terminal starts only on supported Windows builds and uses only the native sandbox", async () => {
  const f = fixture({ terminalPlatformSupported: true }); await f.flush();
  f.button("Terminal").props.onClick(); await f.flush();
  await f.terminal().onStart(); await f.flush();
  assert.equal(f.terminal().interactiveSupported, true);
  assert.equal(f.calls.some(call => call.method === "windowsSandbox/readiness"), true);
  const command = f.calls.find(call => call.method === "command/exec");
  assert.ok(command);
  assert.equal(command.params.tty, true);
  assert.equal(command.params.sandboxPolicy.networkAccess, false);
  assert.equal(command.params.sandboxPolicy.writableRoots.length, 1);
  const unsupported = fixture({ terminalPlatformSupported: false }); await unsupported.flush();
  unsupported.button("Terminal").props.onClick(); await unsupported.flush();
  await unsupported.terminal().onStart(); await unsupported.flush();
  assert.equal(unsupported.terminal().interactiveSupported, false);
  assert.equal(unsupported.calls.some(call => call.method === "command/exec"), false);
  const terminal = readFileSync(new URL("../src/NativeTerminal.tsx", import.meta.url), "utf8");
  assert.match(terminal, /Smara will never start an unsandboxed shell/);
});
