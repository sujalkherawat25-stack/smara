import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";

function fixture({ state = "draft", confirmed = false, allowed = true, locked = false } = {}) {
  const slots = [], effects = [], calls = [], confirmations = [];
  let cursor = 0, dirty = true, tree;
  const draft = { id: "synthetic-draft", content_hash: "exact-reviewed-content", state, detail: "No delivery claimed", draft: {
    company: "Synthetic company", to: "hello@example.org", subject: "Synthetic pilot", text: "The full synthetic message.", evidence: [] } };
  const hooks = {
    useState(initial) { const i = cursor++; if (!(i in slots)) slots[i] = initial; return [slots[i], value => { slots[i] = typeof value === "function" ? value(slots[i]) : value; dirty = true; }]; },
    useEffect(effect, deps) { const i = cursor++, previous = slots[i]; if (!previous || deps.some((value, j) => value !== previous[j])) { slots[i] = deps; effects.push(effect); } },
  };
  const module = { exports: {} };
  const code = ts.transpileModule(readFileSync(new URL("../src/NativeEmail.tsx", import.meta.url), "utf8"), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  vm.runInNewContext(code, { module, exports: module.exports, require(name) {
    if (name === "react") return hooks;
    if (name === "react/jsx-runtime") return { jsx: (type, props) => ({ type, props }), jsxs: (type, props) => ({ type, props }) };
    if (name === "./nativeApi") return { nativeSettings: async () => ({}) };
    if (name === "@tauri-apps/api/core") return { invoke: async (_command, args) => {
      calls.push(args.request); return { status: { enabled: true, client_id: "synthetic", allowlist: allowed ? [draft.draft.to] : [], daily_limit: 5, connected: true, sender: "sender@example.org" }, drafts: [draft] };
    } };
    assert.fail(name);
  }, window: { confirm(message) { confirmations.push(message); return confirmed; } } });
  const props = { workspace: "C:/synthetic", locked, onUpdate() {}, onPendingChange() {} };
  const flush = async () => { for (let n = 0; n < 20; n++) { if (dirty) { dirty = false; cursor = 0; tree = module.exports.default(props); while (effects.length) effects.shift()(); } await Promise.resolve(); } };
  const walk = node => Array.isArray(node) ? node.flatMap(walk) : !node || typeof node !== "object" ? [] : [node, ...(Array.isArray(node.props?.children) ? node.props.children : [node.props?.children]).flatMap(walk)];
  const text = node => Array.isArray(node) ? node.map(text).join("") : node && typeof node === "object" ? text(node.props?.children) : String(node ?? "");
  const button = label => walk(tree).find(node => node.type === "button" && text(node) === label);
  return { calls, confirmations, flush, button, async select() { await flush(); button("Synthetic company · " + state).props.onClick(); await flush(); } };
}

test("declining exact-content review sends no dispatch request", async () => {
  const ui = fixture(); await ui.select();
  ui.button("Review & send one email…").props.onClick(); await ui.flush();
  assert.match(ui.confirmations[0], /hello@example\.org/);
  assert.match(ui.confirmations[0], /The full synthetic message/);
  assert.equal(ui.calls.filter(call => call.outreach.action === "send").length, 0);
});

test("confirmed send carries only the selected draft and exact review hash", async () => {
  const ui = fixture({ confirmed: true }); await ui.select();
  ui.button("Review & send one email…").props.onClick(); await ui.flush();
  const send = ui.calls.find(call => call.outreach.action === "send");
  assert.equal(send.outreach.id, "synthetic-draft");
  assert.equal(send.outreach.content_hash, "exact-reviewed-content");
  assert.equal(send.outreach.text, undefined);
});

test("unapproved cohort is disabled and unknown dispatch offers no resend", async () => {
  const disallowed = fixture({ allowed: false }); await disallowed.select();
  assert.equal(disallowed.button("Review & send one email…").props.disabled, true);
  const unknown = fixture({ state: "unknown" }); await unknown.select();
  assert.equal(unknown.button("Review & send one email…"), undefined);
  assert.ok(unknown.button("Check Gmail Sent"));
});
