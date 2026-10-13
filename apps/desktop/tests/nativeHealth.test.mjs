import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";

function fixture() {
  const slots = [], effects = [], timers = new Set(), requests = [], settings = [];
  let cursor = 0, dirty = true, tree;
  const props = { workspace: "C:/synthetic-first", locked: false, onSettings: section => settings.push(section) };
  const hooks = {
    useState(initial) { const i = cursor++; if (!(i in slots)) slots[i] = initial; return [slots[i], value => { slots[i] = value; dirty = true; }]; },
    useEffect(effect, deps) { const i = cursor++, prev = slots[i]; if (!prev || deps.some((v, n) => v !== prev.deps[n])) {
      slots[i] = { deps }; effects.push(() => { prev?.cleanup?.(); slots[i].cleanup = effect(); });
    } },
  };
  const module = { exports: {} };
  const source = readFileSync(new URL("../src/NativeHealth.tsx", import.meta.url), "utf8");
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  const jsx = (type, props) => ({ type, props });
  vm.runInNewContext(code, { module, exports: module.exports, require(name) {
    if (name === "react") return hooks;
    if (name === "react/jsx-runtime") return { jsx, jsxs: jsx };
    if (name === "@tauri-apps/api/core") return { invoke: (command, args) => new Promise((resolve, reject) => requests.push({ command, args, resolve, reject })) };
    assert.fail(name);
  }, setTimeout: cb => { timers.add(cb); return cb; }, clearTimeout: cb => timers.delete(cb) });
  const visit = value => Array.isArray(value) ? value.flatMap(visit) : value && typeof value === "object" ? [value, ...visit(value.props?.children)] : [];
  const text = value => Array.isArray(value) ? value.map(text).join("") : value && typeof value === "object" ? text(value.props?.children) : String(value ?? "");
  const flush = async () => { for (let n = 0; n < 10; n++) { if (dirty) { dirty = false; cursor = 0; tree = module.exports.default(props); while (effects.length) effects.shift()(); } await Promise.resolve(); } };
  return { props, requests, settings, timers, flush, text: () => text(tree), nodes: () => visit(tree),
    update: async values => { Object.assign(props, values); dirty = true; await flush(); },
    tick: async () => { for (const cb of [...timers]) { timers.delete(cb); cb(); } await flush(); },
    unmount: () => { for (const slot of slots) slot?.cleanup?.(); },
  };
}
const health = (changes = {}) => ({ search: { configured: true }, network_probes: 0,
  memory: { enabled: true, detail: "Locally ready; not live verified", writes: {}, level: "ready", last_activity: null, ...changes } });

test("health uses local status only, preserves failed-write warning and respects settings lock", async () => {
  const f = fixture(); await f.flush();
  assert.equal(f.requests[0].command, "native_manage");
  assert.deepEqual(JSON.parse(JSON.stringify(f.requests[0].args)), { request: { operation: "runtime_health", workspace: "C:/synthetic-first" } });
  f.requests[0].resolve(health({ writes: { failed: 2 }, last_activity: { detail: "Last recall succeeded" } })); await f.flush();
  assert.match(f.text(), /needs attention/); assert.match(f.text(), /2 failed/); assert.match(f.text(), /Not a live search test/);
  await f.update({ locked: true, workspace: "C:/synthetic-second" });
  f.requests[1].resolve(health()); await f.flush();
  assert.ok(f.nodes().filter(n => n.type === "button").every(n => n.props.disabled));
  assert.equal(f.requests.length, 2);
});

test("pending calls do not overlap, and old project replies cannot populate the new project", async () => {
  const f = fixture(); await f.flush(); await f.tick();
  assert.equal(f.requests.length, 1);
  await f.update({ workspace: "C:/synthetic-second" });
  f.requests[0].resolve(health({ writes: { pending: 99 } })); await f.flush();
  assert.doesNotMatch(f.text(), /99 pending/);
  f.requests[1].resolve(health({ writes: { pending: 3, submitted: 2 } })); await f.flush();
  assert.match(f.text(), /5 pending/);
  f.unmount(); await f.tick(); assert.equal(f.requests.length, 2);
});

test("bad metadata or provider exception cannot crash chat or expose raw errors", async () => {
  for (const value of [null, {}, health({ writes: { pending: -1 } }), health({ writes: { failed: NaN } }), { ...health(), network_probes: 1 }]) {
    const f = fixture(); await f.flush(); f.requests[0].resolve(value); await f.flush();
    assert.match(f.text(), /status unavailable/); assert.match(f.text(), /not proof that a provider is down/);
  }
  const f = fixture(); await f.flush(); f.requests[0].reject(Error("synthetic private diagnostic")); await f.flush();
  assert.doesNotMatch(f.text(), /private diagnostic/);
  await f.tick(); assert.equal(f.requests.length, 2);
});
