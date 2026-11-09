import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";

const module = { exports: {} };
const code = ts.transpileModule(readFileSync(new URL("../src/transcriptWindow.ts", import.meta.url), "utf8"), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
vm.runInNewContext(code, { module, exports: module.exports });
const { INITIAL_WINDOW, WINDOW_STEP, visibleWindow, widenWindow, transcriptRowKey } = module.exports;

const rows = (count) => Array.from({ length: count }, (_, index) => ({ id: `item-${index}`, kind: "assistant", text: `row ${index}` }));

test("a long chat renders only its newest rows, and the rest stay behind a button", () => {
  const items = rows(1000);
  const { visible, hidden } = visibleWindow(items, INITIAL_WINDOW);
  assert.equal(visible.length, 60);
  assert.equal(hidden, 940);
  assert.equal(visible[0].id, "item-940");
  assert.equal(visible.at(-1).id, "item-999");
});

test("'show earlier' adds one step at a time, up to the whole chat", () => {
  const items = rows(130);
  let limit = INITIAL_WINDOW;
  assert.equal(widenWindow(limit), limit + WINDOW_STEP);
  limit = widenWindow(limit);
  assert.deepEqual([visibleWindow(items, limit).visible.length, visibleWindow(items, limit).hidden], [120, 10]);
  limit = widenWindow(limit);
  assert.deepEqual([visibleWindow(items, limit).visible.length, visibleWindow(items, limit).hidden], [130, 0]);
});

test("short chats, zero and odd limits never throw or show phantom rows", () => {
  // Compare fields, not whole objects: the module runs in its own realm, so deepStrictEqual sees different prototypes.
  const three = rows(3);
  const full = visibleWindow(three, 60);
  assert.equal(full.hidden, 0);
  assert.equal(full.visible.length, 3);
  assert.equal(full.visible[0], three[0]);
  const none = visibleWindow(rows(3), 0);
  assert.equal(none.visible.length, 0);
  assert.equal(none.hidden, 3);
  assert.equal(visibleWindow(rows(5), -4).visible.length, 0);
  assert.equal(visibleWindow(rows(5), 2.9).visible.length, 2);
  assert.equal(visibleWindow([], 60).hidden, 0);
});

test("a row's key changes only when something it shows changes", () => {
  const base = { id: "a", kind: "commandExecution", status: "inProgress", text: "npm test", command: "npm test" };
  assert.equal(transcriptRowKey(base), transcriptRowKey({ ...base }));
  assert.notEqual(transcriptRowKey(base), transcriptRowKey({ ...base, text: "npm test\nPASS" }));
  assert.notEqual(transcriptRowKey(base), transcriptRowKey({ ...base, status: "completed" }));
  assert.notEqual(transcriptRowKey(base), transcriptRowKey({ ...base, exitCode: 0 }));
  assert.notEqual(transcriptRowKey(base), transcriptRowKey({ ...base, durationMs: 3200 }));
});
