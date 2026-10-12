// Component interaction fixtures; real native execution is checked separately.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";

function fixture(state = "queued", failFeedback = false) {
  const slots = [], effects = [], calls = [];
  let cursor = 0, dirty = true, tree;
  const goal = { id: "syntheticgoal", state, lease: null, session_id: "synthetic-native-session", cycles: 1, requests: 2, token_estimate: 100,
    spec: { objective: "Synthetic goal", success_criteria: "Evidence", workspace: "C:/fixture", profile_id: "fixture", max_cycles: 12, max_requests: 40, max_token_estimate: 750000 },
    checkpoint: {}, approvals: state === "awaiting_approval" ? [{ id: "scoped-native-request", method: "item/commandExecution/requestApproval", params: { command: "synthetic command" } }] : [] };
  const hooks = {
    useState(initial) { const index = cursor++; if (!(index in slots)) slots[index] = initial; return [slots[index], value => { slots[index] = typeof value === "function" ? value(slots[index]) : value; dirty = true; }]; },
    useRef(initial) { const index = cursor++; return slots[index] ??= { current: initial }; },
    useEffect(effect, deps) { const index = cursor++; if (!slots[index]) { slots[index] = deps; effects.push(effect); } },
  };
  const jsx = (type, props) => ({ type, props: props || {} });
  const module = { exports: {} };
  const source = readFileSync(new URL("../src/NativeGoals.tsx", import.meta.url), "utf8");
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true } }).outputText;
  const require = name => {
    if (name === "react") return hooks;
    if (name === "react/jsx-runtime") return { jsx, jsxs: jsx };
    if (name.endsWith("goalApi")) return { goalLabel: value => value, goalRequest: async (action, values) => {
      calls.push({ action, values });
      if (action === "list") return { goals: [goal], service: {} };
      if (action === "feedback" && failFeedback) throw new Error("Synthetic failed feedback save");
      if (action === "create") return { ...goal, id: values.identity, spec: values };
      return goal;
    } };
    if (name.endsWith("nativeView")) return { supportsApproval: method => method === "item/commandExecution/requestApproval" };
    if (name.endsWith("NativeMarkdown")) return { default: () => null, __esModule: true };
    assert.fail("Unexpected import " + name);
  };
  vm.runInNewContext(code, { module, exports: module.exports, require, crypto: { randomUUID: () => "syntheticcreationreceipt" }, setInterval: () => 1, clearInterval() {} });
  const render = () => module.exports.default({ workspace: "C:/fixture", profile: "fixture", locked: false, onClose() {}, onPreview() {} });
  const flush = async () => { for (let index = 0; index < 25; index++) { if (dirty) { dirty = false; cursor = 0; tree = render(); while (effects.length) effects.shift()(); } await Promise.resolve(); } assert.equal(dirty, false); };
  const nodes = () => { const result = []; const walk = node => { if (Array.isArray(node)) node.forEach(walk); else if (node && typeof node === "object") { result.push(node); walk(node.props?.children); } }; walk(tree); return result; };
  const text = node => Array.isArray(node) ? node.map(text).join("") : node && typeof node === "object" ? text(node.props?.children) : String(node ?? "");
  const button = label => nodes().find(node => node.type === "button" && text(node) === label);
  const select = async () => { button("Synthetic goal" + state + " · syntheti").props.onClick(); await flush(); };
  return { calls, flush, nodes, button, select };
}

test("a goal can only be accepted when its result is ready for review", async () => {
  for (const state of ["queued", "running", "blocked", "ready_for_review"]) {
    const f = fixture(state); await f.flush(); await f.select();
    assert.equal(!!f.button("Accept result"), state === "ready_for_review");
  }
});

test("goal creation captures criteria, starts read-only and carries a retry receipt", async () => {
  const f = fixture(); await f.flush(); f.button("＋ New goal").props.onClick(); await f.flush();
  const fields = f.nodes().filter(node => node.type === "textarea");
  fields[0].props.onChange({ target: { value: "Inspect fixture" } }); fields[1].props.onChange({ target: { value: "Show evidence" } }); await f.flush();
  f.nodes().find(node => node.type === "form").props.onSubmit({ preventDefault() {} }); await f.flush();
  const request = f.calls.find(call => call.action === "create");
  assert.equal(request.values.sandbox, "read-only"); assert.equal(request.values.success_criteria, "Show evidence");
  assert.equal(request.values.identity, "syntheticcreationreceipt");
});

test("failed feedback remains in the editor for retry", async () => {
  const f = fixture("queued", true); await f.flush(); await f.select();
  f.nodes().find(node => node.type === "textarea").props.onChange({ target: { value: "Important feedback" } }); await f.flush();
  f.nodes().find(node => node.type === "form").props.onSubmit({ preventDefault() {} }); await f.flush();
  assert.equal(f.nodes().find(node => node.type === "textarea").props.value, "Important feedback");
});

test("Deny sends only the selected goal and native request receipt", async () => {
  const f = fixture("awaiting_approval"); await f.flush(); await f.select();
  f.button("Deny").props.onClick(); await f.flush();
  // The component runs in a separate VM realm; compare its serialized payload.
  assert.deepEqual(JSON.parse(JSON.stringify(f.calls.find(call => call.action === "respond").values)), { id: "syntheticgoal", request_id: "scoped-native-request", allow: false });
});

test("extending a selected goal starts from its saved cumulative limits", async () => {
  const f = fixture("paused"); await f.flush(); await f.select();
  assert.deepEqual(f.nodes().filter(node => node.type === "input" && node.props.type === "number").map(node => node.props.value), [12, 40, 750000]);
  f.button("＋ New goal").props.onClick(); await f.flush();
  assert.deepEqual(f.nodes().filter(node => node.type === "input" && node.props.type === "number").map(node => node.props.value), [8, 30, 500000]);
});
