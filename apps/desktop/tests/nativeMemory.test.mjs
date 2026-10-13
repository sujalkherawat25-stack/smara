// Synthetic component interactions, not installed-GUI or hosted recall evidence.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";

function fixture({ configured = false, enabled = false, locked = false, confirm = true, failure = false } = {}) {
  const slots = [], effects = [], calls = [], confirmations = [], pending = [];
  let cursor = 0, dirty = true, tree;
  const setup = { memory: { enabled, configured, scope: "project", user_id: "synthetic-person", detail: "Local readiness only", writes: {}, last_activity: null }, skills_roots: ["C:/synthetic/.agents/skills"] };
  const props = { setup, locked, onUpdate(value) { props.setup = value; dirty = true; }, onPendingChange(value) { pending.push(value); } };
  const hooks = {
    useState(initial) { const i = cursor++; if (!(i in slots)) slots[i] = initial; return [slots[i], value => { slots[i] = typeof value === "function" ? value(slots[i]) : value; dirty = true; }]; },
    useEffect(effect, deps) { const i = cursor++, previous = slots[i]; if (!previous || deps.some((value, j) => value !== previous[j])) { slots[i] = deps; effects.push(effect); } },
  };
  const source = readFileSync(new URL("../src/NativeMemorySettings.tsx", import.meta.url), "utf8");
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  const module = { exports: {} };
  const api = {
    async nativeSettings(operation, values) {
      calls.push({ operation, values });
      if (failure) throw Error("Synthetic settings failure");
      if (operation === "save_memory") return { ...setup, memory: { ...setup.memory, ...values.memory, configured: configured || !!values.secret } };
      return setup;
    },
    async nativeProbe(operation) { calls.push({ operation }); return { ok: true, detail: "Hosted search responded; reads only." }; },
  };
  vm.runInNewContext(code, { module, exports: module.exports, require(name) {
    if (name === "react") return hooks;
    if (name === "react/jsx-runtime") return { jsx: (type, props) => ({ type, props }), jsxs: (type, props) => ({ type, props }) };
    if (name === "./nativeApi") return api;
    assert.fail(name);
  }, window: { confirm(message) { confirmations.push(message); return confirm; } }, Date });
  const App = module.exports.default;
  function render() { for (let n = 0; dirty && n < 10; n++) { dirty = false; cursor = 0; tree = App(props); while (effects.length) effects.shift()(); } }
  const visit = node => !node || typeof node !== "object" ? [] : [node, ...(Array.isArray(node.props?.children) ? node.props.children : [node.props?.children]).flatMap(visit)];
  const text = node => typeof node === "string" ? node : !node || typeof node !== "object" ? "" : (Array.isArray(node.props?.children) ? node.props.children : [node.props?.children]).map(text).join("");
  const find = predicate => { render(); return visit(tree).find(predicate); };
  return { calls, confirmations, pending, render, find, text: () => { render(); return text(tree); },
    async change(label, value, checked) { find(n => n.props?.["aria-label"] === label || (n.type === "input" && n.props?.type === "checkbox" && label === "enable")).props.onChange({ target: { value, checked } }); render(); },
    async submit() { find(n => n.type === "form").props.onSubmit({ preventDefault() {} }); await new Promise(resolve => setImmediate(resolve)); render(); },
    async click(label) { const button = find(n => n.type === "button" && text(n) === label); button.props.onClick?.(); await new Promise(resolve => setImmediate(resolve)); render(); },
  };
}

test("Memory settings start disabled, show local skills and do not make startup requests", () => {
  const ui = fixture(); ui.render();
  assert.equal(ui.calls.length, 0);
  assert.match(ui.text(), /Disabled/);
  assert.match(ui.text(), /SKILL\.md/);
  assert.equal(ui.find(n => n.props?.["aria-label"] === "Syntarus project key").props.type, "password");
});

test("Saving a key without enabling memory does not authorize uploads", async () => {
  const ui = fixture(); await ui.change("Syntarus project key", "synthetic-secret"); await ui.submit();
  assert.equal(ui.calls[0].operation, "save_memory");
  assert.equal(ui.calls[0].values.memory.enabled, false);
  assert.equal(ui.confirmations.length, 0);
  assert.equal(ui.find(n => n.props?.["aria-label"] === "Syntarus project key").props.value, "");
  assert.deepEqual(ui.pending, [true, false]);
});

test("Enabling personal recall requires explicit consent and retains stable identity", async () => {
  const ui = fixture({ configured: true });
  await ui.change("enable", "", true); await ui.change("Conversation memory scope", "personal"); await ui.submit();
  assert.equal(ui.calls[0].values.memory.scope, "personal");
  assert.equal(ui.calls[0].values.memory.user_id, "synthetic-person");
  assert.match(ui.confirmations[0], /private code/);
  assert.match(ui.confirmations[0], /will not be bulk-uploaded/);
});

test("Declining consent sends no settings write or provider request", async () => {
  const ui = fixture({ configured: true, confirm: false });
  await ui.change("enable", "", true); await ui.submit();
  assert.equal(ui.calls.length, 0);
});

test("Existing memory identity can be selected without importing local history", async () => {
  const ui = fixture({ configured: true }); await ui.change("Syntarus memory user ID", "existing-synthetic-user"); await ui.submit();
  assert.equal(ui.calls[0].values.memory.user_id, "existing-synthetic-user");
  assert.match(ui.text(), /does not migrate memories/);
});

test("Connection probe confirms a fixed synthetic read and has no save side effect", async () => {
  const ui = fixture({ configured: true }); await ui.click("Test memory connection");
  assert.deepEqual(ui.calls, [{ operation: "test_memory" }]);
  assert.match(ui.confirmations[0], /No personal conversation/);
  assert.match(ui.text(), /reads only/);
});

test("Failed save clears the secret and releases the UI lock", async () => {
  const ui = fixture({ failure: true }); await ui.change("Syntarus project key", "synthetic-secret"); await ui.submit();
  assert.match(ui.text(), /Synthetic settings failure/);
  assert.equal(ui.find(n => n.props?.["aria-label"] === "Syntarus project key").props.value, "");
  assert.deepEqual(ui.pending, [true, false]);
});

test("Locked settings cannot save or test a connection", async () => {
  const ui = fixture({ configured: true, locked: true }); await ui.submit(); await ui.click("Test memory connection");
  assert.equal(ui.calls.length, 0);
});
