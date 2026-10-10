// Synthetic React-hook/transport harness. Installed GUI acceptance is separate.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";

function fixture({ startupFailure = false, unknownTurn = false } = {}) {
  const slots = [], calls = [], effects = [];
  let cursor = 0, dirty = true, tree, failResume = false;
  const setup = {
    preferences: { workspace: "C:/first", active_model: "one", projects: [{ workspace: "C:/first", label: "First" }, { workspace: "C:/second", label: "Second" }], search_provider: "tavily" },
    profiles: [{ id: "one", label: "Offline one", model: "offline-one" }, { id: "two", label: "Offline two", model: "offline-two" }], credentials: [], search: { configured: false },
  };
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
    vm.runInNewContext(code, { module, exports: module.exports, require, window: { addEventListener() {}, removeEventListener() {} }, navigator: { clipboard: { writeText: async () => {} } } });
    return module.exports;
  };
  class RpcOutcomeUnknown extends Error {}
  class NativeRpc {
    generation = "";
    constructor(receive) { this.receive = receive; }
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
  function Settings() {}
  const App = evaluate("../src/NativeApp.tsx", name => {
    if (name === "react") return hooks;
    if (name === "react/jsx-runtime") return { jsx, jsxs: jsx };
    if (name === "@tauri-apps/api/core") return { invoke: async () => ({ built: true }) };
    if (name.endsWith("NativeSettings")) return { default: Settings, __esModule: true };
    if (name.endsWith("NativeMarkdown")) return { default: () => null, __esModule: true };
    if (name.endsWith("nativeApi")) return api;
    if (name.endsWith("nativeRpc")) return { NativeRpc, RpcOutcomeUnknown };
    if (name.endsWith("nativeView")) return view;
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
  return { calls, flush, button, nodes, enter, failResume: () => { failResume = true; }, settings: () => nodes().find(node => node.type === Settings).props, model: () => nodes().find(node => node.type === "select" && node.props["aria-label"] === "Model") };
}

test("startup prepares a project without sending a turn or enabling optional tools", async () => {
  const f = fixture(); await f.flush();
  const connect = f.calls.find(call => call.method === "connect");
  assert.equal(connect.profile, "one");
  assert.equal(connect.readers, false); assert.equal(connect.workers, false); assert.equal(connect.origins.length, 0);
  assert.equal(f.calls.filter(call => call.method === "connect").length, 1);
  assert.equal(f.calls.some(call => call.method === "turn/start"), false);
  assert.equal(f.nodes().some(node => node.type === "button" && ["Connect", "Disconnect", "Manage settings"].includes(node.props.children)), false);
  assert.ok(f.model());
  const composer = f.nodes().find(node => node.type === "form" && node.props.className === "native-composer");
  assert.equal(composer.props.children[1].props.children[1].props.children[0], f.model());
});

test("the primary UI has no technical badge, tool dropdown or capabilities tab", () => {
  const app = readFileSync(new URL("../src/NativeApp.tsx", import.meta.url), "utf8");
  const settings = readFileSync(new URL("../src/NativeSettings.tsx", import.meta.url), "utf8");
  assert.doesNotMatch(app, /sandbox-badge|toolsOpen|Manage settings|native-connection/);
  assert.doesNotMatch(settings, /tab === "capabilities"|"Capabilities"/);
  assert.match(settings, /action approvals remain required/);
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
