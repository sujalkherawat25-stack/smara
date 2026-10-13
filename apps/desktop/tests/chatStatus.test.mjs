import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";
const module = { exports: {} };
const code = ts.transpileModule(readFileSync(new URL("../src/chatStatus.ts", import.meta.url), "utf8"), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
vm.runInNewContext(code, { module, exports: module.exports });
const { waitingReason, requestQuestions } = module.exports;
test("quiet provider/tool periods are waiting, not a proven freeze or fabricated progress", () => {
  assert.match(waitingReason([], 0, 60000, 0), /no new events for 60s/);
  assert.match(waitingReason([], 1, 60000, 0), /not approved/);
  assert.match(waitingReason([{ id: "x", kind: "commandExecution", status: "inProgress" }], 0, 10000, 0), /Running a command/);
  assert.match(waitingReason([{ id: "x", kind: "collabAgentToolCall", status: "inProgress" }], 0, 10000, 0), /worker results/);
});
test("question parser rejects malformed, oversized-count and duplicate native questions", () => {
  for (const questions of [null, {}, [], new Array(21).fill({ id: "x", question: "x" }), [{ id: "x", question: "x", options: {} }], [{ id: "x", question: "x", isSecret: "true" }]]) assert.equal(requestQuestions({ questions }), undefined);
  assert.equal(requestQuestions({ questions: [{ id: "x", question: "x", options: [{ label: "yes", description: "safe" }] }] }).length, 1);
});
