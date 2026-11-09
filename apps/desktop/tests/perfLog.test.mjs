import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";

const source = ts.transpileModule(readFileSync(new URL("../src/perfLog.ts", import.meta.url), "utf8"), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;

function load(storage) {
  const module = { exports: {} };
  const context = { module, exports: module.exports, console };
  if (storage !== undefined) context.localStorage = { getItem: key => storage[key] ?? null };
  vm.runInNewContext(source, context);
  return module.exports;
}

test("perf logging is off unless localStorage.smaraPerf is exactly \"1\"", () => {
  assert.equal(load(undefined).perfEnabled(), false);
  assert.equal(load({}).perfEnabled(), false);
  assert.equal(load({ smaraPerf: "0" }).perfEnabled(), false);
  assert.equal(load({ smaraPerf: "1" }).perfEnabled(), true);
});

test("a storage read that throws leaves perf logging off instead of breaking the UI", () => {
  const module = { exports: {} };
  const context = { module, exports: module.exports, console, localStorage: { getItem() { throw new Error("blocked"); } } };
  vm.runInNewContext(source, context);
  assert.equal(module.exports.perfEnabled(), false);
});
