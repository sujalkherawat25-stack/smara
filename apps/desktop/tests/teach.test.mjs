import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";
const load = file => {
  const module = { exports: {} };
  const code = ts.transpileModule(readFileSync(new URL(`../src/${file}`, import.meta.url), "utf8"), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
  vm.runInNewContext(code, { module, exports: module.exports });
  return module.exports;
};
const t = load("teach.ts");

test("addresses get https and bad ones are caught", () => {
  assert.equal(t.normalizeUrl(" example.com/orders "), "https://example.com/orders");
  assert.equal(t.normalizeUrl("http://a.com"), "http://a.com");
  assert.equal(t.normalizeUrl(""), "");
  assert.equal(t.startProblem("", "kiran"), "Type the website where the job starts.");
  assert.equal(t.startProblem("example.com", ""), "Pick who you are teaching.");
  assert.equal(t.startProblem("nonsense", "kiran"), "Type a website address, like example.com.");
  assert.equal(t.startProblem("example.com", "kiran"), "");
});

test("the status line says what to do next", () => {
  const base = { detail: "", start_url: "", steps: [], count: 0 };
  assert.match(t.teachLine({ ...base, state: "idle" }), /press Start/);
  assert.match(t.teachLine({ ...base, state: "recording" }), /press Done/);
  assert.match(t.teachLine({ ...base, state: "stopped" }), /Nothing was recorded/);
  assert.match(t.teachLine({ ...base, state: "stopped", count: 1 }), /1 step recorded/);
  assert.match(t.teachLine({ ...base, state: "stopped", count: 3 }), /3 steps recorded/);
  assert.equal(t.teachLine({ ...base, state: "failed", detail: "Browser busy" }), "Browser busy");
});
