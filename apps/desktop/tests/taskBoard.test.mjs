import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";
const module = { exports: {} };
const code = ts.transpileModule(readFileSync(new URL("../src/taskBoardView.ts", import.meta.url), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
vm.runInNewContext(code, { module, exports: module.exports });
const v = module.exports;
const goal = (over) => ({ id: "g", state: "running", lease: "l", updated: 1, cycles: 1, requests: 1, token_estimate: 0, detail: "",
  session_id: null, checkpoint: {}, spec: { objective: "Check the GST portal", workspace: "C:\\Shop", sandbox: "read-only", max_cycles: 8 }, ...over });

test("tasks land in the column that says what they need", () => {
  assert.equal(v.columnOf(goal()), "working");
  assert.equal(v.columnOf(goal({ state: "awaiting_approval" })), "needs_you");
  assert.equal(v.columnOf(goal({ state: "ready_for_review" })), "needs_you");
  assert.equal(v.columnOf(goal({ state: "queued", approvals: [{ id: "r", method: "m", params: {} }] })), "needs_you");
  assert.equal(v.columnOf(goal({ state: "blocked" })), "stopped");
  assert.equal(v.columnOf(goal({ state: "completed" })), "done");
  const columns = v.byColumn([goal({ id: "a", updated: 1 }), goal({ id: "b", updated: 5 }), goal({ id: "c", state: "queued" })]);
  assert.equal(JSON.stringify(columns.working.map(g => g.id)), JSON.stringify(["b", "a"]));
  assert.equal(JSON.stringify(columns.queued.map(g => g.id)), JSON.stringify(["c"]));
});

test("approvals and the input holder read as plain words", () => {
  assert.equal(v.approvalText({ method: "mcpServer/elicitation/request", params: { message: "Allow computer_act_confirmed: click Send?" } }), "Allow computer_act_confirmed: click Send?");
  assert.equal(v.approvalText({ method: "item/fileChange/requestApproval", params: {} }), "Change files in the project");
  assert.match(v.approvalText({ method: "item/commandExecution/requestApproval", params: { command: "dir" } }), /Run a command: dir/);
  assert.equal(v.inputLine(null), "Nobody is using the mouse and keyboard.");
  assert.equal(v.inputLine({ label: "Kiran: day book" }), "Kiran: day book is using the mouse and keyboard.");
  assert.equal(v.projectName("c:\\shop", [{ workspace: "C:\\Shop", label: "Sharma Traders" }]), "Sharma Traders");
  assert.equal(v.projectName("C:\\Work\\Books", []), "Books");
});

test("controls follow the state", () => {
  assert.equal(v.canPause(goal()), true);
  assert.equal(v.canResume(goal({ state: "paused", lease: null })), true);
  assert.equal(v.canResume(goal({ state: "paused" })), false);             // still releasing its session
  assert.equal(v.canStop(goal({ state: "completed" })), false);
  assert.equal(v.canDismiss(goal({ state: "stopped", lease: null })), true);
  assert.ok(v.title(goal({ spec: { objective: "x".repeat(200) } })).length <= 90);
});

test("the board creates tasks with an agent and the owner's choices, and asks before Stop", () => {
  const board = readFileSync(new URL("../src/NativeTaskBoard.tsx", import.meta.url), "utf8");
  assert.match(board, /agent_id: form\.agent, use_apps: form\.apps/);
  assert.match(board, /sandbox: form\.edit \? "workspace-write" : "read-only"/);
  assert.match(board, /window\.confirm\("Stop this task\?/);
  assert.match(board, /act\("set_parallel", \{ max_parallel:/);
  assert.match(board, /operation: "audit", action: "list", task: open/);
  const app = readFileSync(new URL("../src/NativeApp.tsx", import.meta.url), "utf8");
  assert.match(app, /panel === "board" && <NativeTaskBoard/);
});
