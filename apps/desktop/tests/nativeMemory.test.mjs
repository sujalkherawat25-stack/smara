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

test("Memory settings show how to connect, list local skills and make no startup requests", () => {
  const ui = fixture(); ui.render();
  assert.equal(ui.calls.length, 0);
  assert.match(ui.text(), /Add your key to turn it on/);
  assert.match(ui.text(), /SKILL\.md/);
  assert.equal(ui.find(n => n.props?.["aria-label"] === "Syntarus project key").props.type, "password");
  assert.equal(ui.find(n => n.type === "input" && n.props?.type === "checkbox"), undefined);   // no on/off switch to forget about
});

test("Saving a key connects and turns memory on in one step, without a confirmation pop-up", async () => {
  const ui = fixture(); await ui.change("Syntarus project key", "synthetic-secret"); await ui.submit();
  assert.equal(ui.calls[0].operation, "save_memory");
  assert.equal(ui.calls[0].values.memory.enabled, true);
  assert.equal(ui.confirmations.length, 0);
  assert.equal(ui.find(n => n.props?.["aria-label"] === "Syntarus project key").props.value, "");
  assert.deepEqual(ui.pending, [true, false]);
  assert.match(ui.text(), /Syntarus memory is on/);
});

test("A saved key whose memory was left off is switched back on by the app", () => {
  const source = readFileSync(new URL("../src/NativeMemorySettings.tsx", import.meta.url), "utf8");
  assert.match(source, /return !!memory\?\.configured && !memory\.enabled/);
});

test("Scope and identity changes keep the stable identity and need no consent pop-up", async () => {
  const ui = fixture({ configured: true });
  await ui.change("Conversation memory scope", "personal"); await ui.click("Save scope and ID");
  assert.equal(ui.calls[0].values.memory.scope, "personal");
  assert.equal(ui.calls[0].values.memory.user_id, "synthetic-person");
  assert.equal(ui.calls[0].values.memory.enabled, true);
  assert.equal(ui.confirmations.length, 0);
});

test("Existing memory identity can be selected without importing local history", async () => {
  const ui = fixture({ configured: true }); await ui.change("Syntarus memory user ID", "existing-synthetic-user"); await ui.click("Save scope and ID");
  assert.equal(ui.calls[0].values.memory.user_id, "existing-synthetic-user");
  assert.match(ui.text(), /does not migrate memories/);
});

test("Connection probe confirms a fixed synthetic read and has no save side effect", async () => {
  const ui = fixture({ configured: true }); await ui.click("Test connection");
  assert.deepEqual(ui.calls, [{ operation: "test_memory" }]);
  assert.match(ui.confirmations[0], /No personal conversation/);
  assert.match(ui.text(), /reads only/);
});

test("Disconnecting asks first, then removes the key", async () => {
  const declined = fixture({ configured: true, confirm: false }); await declined.click("Disconnect");
  assert.equal(declined.calls.length, 0);
  const ui = fixture({ configured: true }); await ui.click("Disconnect");
  assert.equal(JSON.stringify(ui.calls), JSON.stringify([{ operation: "delete_credential", values: { name: "SYNTARUS_API_KEY" } }]));
});

test("Failed save clears the secret and releases the UI lock", async () => {
  const ui = fixture({ failure: true }); await ui.change("Syntarus project key", "synthetic-secret"); await ui.submit();
  assert.match(ui.text(), /Synthetic settings failure/);
  assert.equal(ui.find(n => n.props?.["aria-label"] === "Syntarus project key").props.value, "");
  assert.deepEqual(ui.pending, [true, false]);
});

test("Locked settings cannot save or test a connection", async () => {
  const ui = fixture({ configured: true, locked: true }); await ui.submit(); await ui.click("Test connection");
  assert.equal(ui.calls.length, 0);
});
