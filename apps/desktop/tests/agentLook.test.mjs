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
const { agentTone, contactTime } = load("../src/agents.ts");

test("built-in agents keep their avatar colour, and other agents get a stable one from the palette", () => {
  assert.equal(agentTone({ id: "kiran" }), "teal");
  assert.equal(agentTone({ id: "rohan" }), "amber");
  const first = agentTone({ id: "grace-the-clerk" });
  assert.ok(["teal", "amber", "blue", "violet"].includes(first));
  assert.equal(agentTone({ id: "grace-the-clerk" }), first, "the same agent always gets the same colour");
});

test("a chat-list time reads as a clock time today, Yesterday, or a date before that", () => {
  const now = new Date(2026, 10, 9, 14, 0).getTime();
  assert.equal(contactTime(0, now), "", "no time means no label");
  const today = contactTime(new Date(2026, 10, 9, 9, 30).getTime(), now);
  assert.match(today, /9:30/, "today shows the clock time");
  assert.equal(contactTime(new Date(2026, 10, 8, 18, 0).getTime(), now), "Yesterday");
  const older = contactTime(new Date(2026, 9, 1, 10, 0).getTime(), now);
  assert.notEqual(older, "Yesterday");
  assert.match(older, /Oct/, "older chats show the month");
});
