// Synthetic component transport tests. Real Git actions are tested in pytest.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";

function fixture({ locked = false, confirmed = true } = {}) {
  const slots = [], effects = [], calls = [], busy = [];
  let cursor = 0, dirty = true, tree;
  const hooks = {
    useState(initial) { const i = cursor++; if (!(i in slots)) slots[i] = initial; return [slots[i], value => { slots[i] = typeof value === "function" ? value(slots[i]) : value; dirty = true; }]; },
    useRef(initial) { const i = cursor++; return slots[i] ??= { current: initial }; },
    useEffect(effect, deps) { const i = cursor++, old = slots[i]; if (!old || deps.some((v, n) => v !== old.deps[n])) { slots[i] = { deps }; effects.push(() => { old?.cleanup?.(); slots[i].cleanup = effect(); }); } },
  };
  const jsx = (type, props) => ({ type, props: props ?? {} });
  const module = { exports: {} };
  const code = ts.transpileModule(readFileSync(new URL("../src/NativeChanges.tsx", import.meta.url), "utf8"), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  const invoke = async (_command, { request }) => {
    calls.push(request);
    if (request.operation === "status") return { token: "preview", view: request.view, files: [{ path: "example.py", hunks: 1, added: 1, removed: 1 }], notes: [] };
    if (request.operation === "file") return { path: "example.py", patch: "diff --git a/example.py b/example.py\n--- a/example.py\n+++ b/example.py\n@@ -1 +1 @@\n-before\n+after\n" };
    return { message: "Synthetic action response", recovery: "receipt" };
  };
  vm.runInNewContext(code, { module, exports: module.exports, window: { confirm: () => confirmed }, require: name => name === "react" ? hooks : name === "react/jsx-runtime" ? { jsx, jsxs: jsx } : { invoke } });
  const nodes = () => { const found = []; const visit = n => { if (Array.isArray(n)) n.forEach(visit); else if (n && typeof n === "object") { found.push(n); visit(n.props?.children); } }; visit(tree); return found; };
  const text = n => Array.isArray(n) ? n.map(text).join("") : n && typeof n === "object" ? text(n.props.children) : String(n ?? "");
  const button = label => { const n = nodes().find(n => n.type === "button" && text(n) === label); assert.ok(n, label); return n; };
  const flush = async () => { for (let i = 0; i < 20; i++) { if (dirty) { dirty = false; cursor = 0; tree = module.exports.default({ workspace: "C:/fixture", threadId: "chat", locked, revision: 0, notice: "", onBusyChange: v => busy.push(v), onClose() {} }); while (effects.length) effects.shift()(); } await Promise.resolve(); } assert.equal(dirty, false); };
  return { calls, busy, button, flush, nodes, parts: module.exports.diffParts };
}

test("review writes require confirmation and respect native activity lock", async () => {
  for (const options of [{ locked: true }, { confirmed: false }]) {
    const f = fixture(options); await f.flush(); f.button("Stage all (1)").props.onClick(); await f.flush();
    assert.equal(f.calls.some(call => call.operation === "apply"), false);
  }
});

test("hunk actions are token-bound and recovery uses its receipt", async () => {
  const f = fixture(); await f.flush(); f.button("example.py+1 −1").props.onClick(); await f.flush();
  f.button("Revert hunk").props.onClick(); f.button("Revert hunk").props.onClick(); await f.flush();
  const writes = f.calls.filter(call => call.operation === "apply");
  assert.equal(writes.length, 1);
  assert.equal(writes[0].token, "preview"); assert.equal(writes[0].path, "example.py"); assert.equal(writes[0].hunk, 0); assert.equal(writes[0].confirmed, true);
  assert.deepEqual(f.busy, [true, false]);
  f.button("Restore last recovery").props.onClick(); await f.flush();
  assert.equal(f.calls.find(call => call.operation === "recover").recovery, "receipt");
});

test("branch comparisons expose no stage, revert or undo controls", async () => {
  const f = fixture(); await f.flush(); f.button("Branch").props.onClick(); await f.flush();
  f.button("example.py+1 −1").props.onClick(); await f.flush();
  assert.equal(f.nodes().some(n => n.type === "button" && /^(Stage|Revert|Undo|Unstage) (all|file|hunk)/.test(String(n.props.children))), false);
  assert.equal(f.calls.at(-1).operation, "file");
});

test("diff splitting preserves hunk text and no-newline markers", () => {
  const f = fixture();
  const value = "diff --git a/a b/a\n--- a/a\n+++ b/a\n@@ -1 +1 @@\n-old\n+new\n\\ No newline at end of file\n@@ -9 +9 @@\n-last\n+end\n";
  const parts = f.parts(value);
  assert.equal(parts.hunks.length, 2);
  assert.equal(parts.header + parts.hunks.join(""), value);
});
