// Component interaction contracts, not an installed GUI or OS isolation proof.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";

function fixture(file, consent = true) {
  const slots = [], effects = [], calls = [], pending = [], confirmations = [];
  let cursor = 0, dirty = true, tree;
  const browser = { options: { visible: false, transfers: false, max_actions: 80, seconds: 900 }, sessions: [{ id: "owned-browser", paused: true }] };
  const computer = { selected: { target: { title: "Synthetic editor" } }, targets: [{ id: "returned-window", title: "Synthetic editor" }], detail: "Experimental" };
  const background = { service: { running: false }, startup: false, schedules: [{ id: "owned-wake", name: "Synthetic recurring task", enabled: 0, runs: 0, max_runs: 2, due: 0, detail: "Paused" }] };
  const hooks = {
    useState(initial) { const i = cursor++; if (!(i in slots)) slots[i] = initial; return [slots[i], value => { slots[i] = typeof value === "function" ? value(slots[i]) : value; dirty = true; }]; },
    useRef(initial) { const i = cursor++; if (!(i in slots)) slots[i] = { current: initial }; return slots[i]; },
    useEffect(effect, deps) { const i = cursor++, prior = slots[i]; if (!prior || deps.some((v, j) => v !== prior[j])) { slots[i] = deps; effects.push(effect); } },
  };
  const source = readFileSync(new URL(`../src/${file}.tsx`, import.meta.url), "utf8");
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  const module = { exports: {} };
  vm.runInNewContext(code, { module, exports: module.exports, require(name) {
    if (name === "react") return hooks;
    if (name === "react/jsx-runtime") return { jsx: (type, props) => ({ type, props }), jsxs: (type, props) => ({ type, props }) };
    if (name === "@tauri-apps/api/core") return { async invoke(_command, { request }) { calls.push(request); return request.operation === "browser_control" ? browser : request.operation === "computer_control" ? computer : background; } };
    assert.fail(name);
  }, window: { confirm(value) { confirmations.push(value); return consent; } }, Date });
  const props = { workspace: "C:/synthetic", profile: "synthetic", locked: false, onPendingChange(value) { pending.push(value); } };
  function render() { for (let n = 0; dirty && n < 10; n++) { dirty = false; cursor = 0; tree = module.exports.default(props); while (effects.length) effects.shift()(); } }
  const visit = node => Array.isArray(node) ? node.flatMap(visit) : !node || typeof node !== "object" ? [] : [node, ...visit(node.props?.children)];
  const text = node => Array.isArray(node) ? node.map(text).join("") : typeof node === "string" ? node : !node || typeof node !== "object" ? "" : text(node.props?.children);
  const settle = async () => { await new Promise(resolve => setImmediate(resolve)); render(); };
  return { calls, pending, confirmations, props, render, settle,
    async click(label) { render(); const button = visit(tree).find(n => n.type === "button" && text(n) === label); assert.ok(button, label); assert.ok(!button.props.disabled); button.props.onClick(); await settle(); },
  };
}

test("Browser setup reads status only and denial cannot resume login or change transfer authority", async () => {
  const ui = fixture("NativeAutomation", false); ui.render(); await ui.settle();
  assert.equal(ui.calls.length, 1); assert.equal(ui.calls[0].action, "status");
  await ui.click("Resume owned browser"); await ui.click("Save browser choices");
  assert.equal(ui.calls.length, 1); assert.equal(ui.confirmations.length, 2);
  assert.deepEqual(ui.pending, [true, false]);
});

test("Computer selection uses a returned window and Stop revokes without a new approval", async () => {
  const ui = fixture("NativeAutomation"); ui.render(); await ui.settle();
  await ui.click("Refresh eligible app windows"); await ui.click("Synthetic editor");
  assert.equal(ui.calls.at(-1).action, "select"); assert.equal(ui.calls.at(-1).id, "returned-window");
  await ui.click("Stop computer control"); assert.equal(ui.calls.at(-1).action, "stop");
  assert.equal(ui.confirmations.length, 1);
});

test("A schedule remains paused when enable confirmation is declined", async () => {
  const ui = fixture("NativeBackground", false); ui.render(); await ui.settle();
  assert.equal(ui.calls[0].operation, "background"); assert.equal(ui.calls[0].action, "status");
  await ui.click("Enable schedule"); assert.equal(ui.calls.length, 1);
  assert.match(ui.confirmations[0], /charges/);
});

test("Settings checkboxes stay beside their labels, not stretched like text fields", () => {
  const css = readFileSync(new URL("../src/native-shell.css", import.meta.url), "utf8");
  assert.match(css, /\.settings-form label:has\(> input\[type="checkbox"\]\)\s*\{[^}]*flex-direction:\s*row/);
  assert.match(css, /\.settings-form input\[type="checkbox"\]\s*\{[^}]*width:\s*auto/);
});
