import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";

const load = (path) => {
  const module = { exports: {} };
  const code = ts.transpileModule(readFileSync(new URL(path, import.meta.url), "utf8"), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
  vm.runInNewContext(code, { module, exports: module.exports, require: () => { throw new Error("no imports allowed"); } });
  return module.exports;
};
const { toolSummary, groupTranscript } = load("../src/toolCards.ts");
// Results come from another realm; compare as plain data.
const plain = (value) => JSON.parse(JSON.stringify(value));

test("a finished command reads as one line with its status and duration", () => {
  const summary = toolSummary({ id: "c1", kind: "commandExecution", text: "", command: "npm test", status: "completed", exitCode: 0, durationMs: 3200 });
  assert.equal(summary.line, "Ran npm test · 3.2 s");
  assert.equal(summary.state, "ok");
});

test("a failed command shows its exit code and stays a failed row", () => {
  const summary = toolSummary({ id: "c2", kind: "commandExecution", text: "", command: "pytest -q", status: "failed", exitCode: 1, durationMs: 740 });
  assert.equal(summary.line, "Ran pytest -q · exit 1 · 740 ms");
  assert.equal(summary.state, "failed");
});

test("a running command says so, and no duration is shown before it finishes", () => {
  const summary = toolSummary({ id: "c3", kind: "commandExecution", text: "", command: "npm run build", status: "inProgress" });
  assert.equal(summary.line, "Ran npm run build");
  assert.equal(summary.state, "running");
});

test("long commands are cut to one line", () => {
  const summary = toolSummary({ id: "c4", kind: "commandExecution", text: "", command: "x".repeat(200), status: "completed", exitCode: 0 });
  assert.ok(summary.target.length <= 80 && summary.target.endsWith("…"));
  assert.ok(!summary.line.includes("\n"));
});

test("file edits and searches use plain verbs, and other tools show their first line", () => {
  assert.equal(toolSummary({ id: "f", kind: "fileChange", text: "invoice.xlsx\n+ row", status: "completed" }).line, "Edited · invoice.xlsx");
  assert.equal(toolSummary({ id: "s", kind: "webSearch", text: "Tally export guide", status: "completed" }).verb, "Searched the web");
  assert.equal(toolSummary({ id: "m", kind: "someNewTool", text: "first line\nsecond", status: "completed" }).target, "first line");
});

test("consecutive tool rows collapse into one group, and messages break the run", () => {
  const items = [
    { id: "u", kind: "user", text: "check" },
    { id: "t1", kind: "commandExecution", text: "", command: "a", status: "completed", exitCode: 0 },
    { id: "t2", kind: "commandExecution", text: "", command: "b", status: "inProgress" },
    { id: "t3", kind: "fileChange", text: "x", status: "completed" },
    { id: "a", kind: "assistant", text: "done" },
    { id: "t4", kind: "webSearch", text: "q", status: "completed" },
  ];
  const segments = plain(groupTranscript(items));
  assert.deepEqual(segments.map(segment => segment.type), ["item", "group", "item", "item"]);
  assert.equal(segments[1].items.length, 3);
  assert.equal(segments[1].running, true);
  assert.equal(segments[1].key, "t1");
});

test("a finished group is collapsed (not running) and a single tool row is not grouped", () => {
  const done = plain(groupTranscript([
    { id: "a", kind: "commandExecution", text: "", command: "a", status: "completed", exitCode: 0 },
    { id: "b", kind: "commandExecution", text: "", command: "b", status: "completed", exitCode: 0 },
  ]));
  assert.equal(done[0].running, false);
  assert.equal(plain(groupTranscript([{ id: "solo", kind: "webSearch", text: "q" }]))[0].type, "item");
});

test("history dividers are neither tool steps nor grouped", () => {
  const segments = plain(groupTranscript([
    { id: "s", kind: "separator", text: "Earlier" },
    { id: "a", kind: "commandExecution", text: "", command: "a", status: "completed", exitCode: 0 },
  ]));
  assert.deepEqual(segments.map(segment => segment.type), ["item", "item"]);
});
