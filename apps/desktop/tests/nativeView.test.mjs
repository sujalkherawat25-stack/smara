import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";
const module = { exports: {} };
const code = ts.transpileModule(readFileSync(new URL("../src/nativeView.ts", import.meta.url), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
vm.runInNewContext(code, { module, exports: module.exports });
const { displayItem, displayRuntimeError, mergeItem, mergeWorkers, workerActivity, workerSnapshot, approvalResponse, activeTurn, routeMessage, answerKey, threadLabel, resumeStatus, supportsApproval } = module.exports;
const plain = value => JSON.parse(JSON.stringify(value));

test("output-token exhaustion is reported as an incomplete model step, not a setup failure", () => {
  const message = displayRuntimeError("stream disconnected before completion: Incomplete response returned, reason: max_output_tokens");
  assert.match(message, /response-token limit/);
  assert.match(message, /Incomplete tool calls were not executed/);
  assert.match(message, /Continue to inspect completed work/);
  assert.equal(displayRuntimeError("provider unavailable"), "provider unavailable");
});

test("known child progress is visible without changing parent completion or showing its text", () => {
  const workers = [{ id: "child", status: "running" }];
  const message = { method: "item/agentMessage/delta", params: { threadId: "child", delta: "private child transcript" } };
  const updated = workerActivity(workers, message, 42);
  assert.equal(updated[0].activity, "Writing response");
  assert.equal(updated[0].status, "running");
  assert.equal(updated[0].lastActivityAt, 42);
  assert.ok(!JSON.stringify(updated).includes("private child"));
  assert.equal(workerActivity(workers, { ...message, params: { threadId: "unrelated" } }, 42), workers);
  assert.equal(workerActivity(workers, { method: "turn/completed", params: { threadId: "child", turn: { status: "failed" } } }, 43)[0].status, "failed");
});

test("summary polling does not manufacture activity for unchanged workers", () => {
  const turn = { id: "turn", status: "inProgress", items: [{ id: "first" }] };
  let workers = workerSnapshot([{ id: "child", status: "running" }], "child", turn, 10);
  workers = workerSnapshot(workers, "child", turn, 20);
  assert.equal(workers[0].lastActivityAt, 10);
  workers = workerSnapshot(workers, "child", { ...turn, status: "completed" }, 30);
  assert.equal(workers[0].status, "completed");
  assert.equal(workers[0].lastActivityAt, 30);
  const latePoll = workerSnapshot(workers, "child", turn, 40);
  assert.equal(latePoll[0].status, "completed");
  assert.equal(latePoll[0].lastActivityAt, 30);
});

test("native command metadata stays separate from streamed output and keeps nonzero exit codes", () => {
  const start = displayItem({ id: "cmd", type: "commandExecution", command: "pytest", cwd: "C:/fixture", status: "inProgress", aggregatedOutput: "" });
  let items = mergeItem([], start);
  items = mergeItem(items, { id: "cmd", kind: "commandExecution", text: "actual failing test output" }, true);
  items = mergeItem(items, displayItem({ id: "cmd", type: "commandExecution", command: "pytest", cwd: "C:/fixture", status: "completed", exitCode: 1, durationMs: 1400, aggregatedOutput: "" }));
  assert.equal(items[0].text, "actual failing test output");
  assert.equal(items[0].command, "pytest"); assert.equal(items[0].cwd, "C:/fixture");
  assert.equal(items[0].exitCode, 1); assert.equal(items[0].durationMs, 1400);
});

test("worker cards use native worker state, not completion of the spawn tool", () => {
  const item = { type: "collabAgentToolCall", status: "completed", receiverThreadIds: ["a", "b"], agentsStates: { a: { status: "running" } } };
  const workers = mergeWorkers([], item);
  assert.deepEqual(plain(workers), [{ id: "a", status: "running" }, { id: "b", status: "status unknown" }]);
  assert.equal(mergeWorkers(workers, { type: "agentMessage", text: "b completed" }), workers);
  assert.deepEqual(plain(mergeWorkers([{ id: "a", status: "running", cwd: "synthetic" }], { ...item, receiverThreadIds: ["a"], agentsStates: { a: { status: "failed" } } })), [{ id: "a", status: "failed", cwd: "synthetic" }]);
});

test("resumed user messages render text, not protocol JSON", () => {
  assert.equal(displayItem({ id: "u", type: "userMessage", content: [{ type: "text", text: "hello" }, { type: "image" }] }).text, "hello\n[image]");
});
test("late started and empty completed events do not erase streamed output", () => {
  const streamed = [{ id: "c", kind: "commandExecution", text: "actual output" }];
  const started = mergeItem(streamed, { id: "c", kind: "commandExecution", text: "command" }, false, true);
  assert.equal(started[0].text, "actual output");
  assert.equal(mergeItem(started, { id: "c", kind: "commandExecution", text: "" })[0].text, "actual output");
  assert.equal(mergeItem(started, { id: "c", kind: "commandExecution", text: " final" }, true)[0].text, "actual output final");
});
test("permission decisions grant only the request and only for this turn", () => {
  const requested = { network: { enabled: true }, fileSystem: { read: ["synthetic"] } };
  assert.deepEqual(plain(approvalResponse("item/permissions/requestApproval", { permissions: requested }, true)), { permissions: requested, scope: "turn" });
  assert.deepEqual(plain(approvalResponse("item/permissions/requestApproval", { permissions: requested }, false)), { permissions: {}, scope: "turn" });
  assert.throws(() => approvalResponse("item/permissions/requestApproval", { permissions: { unknown: true } }, true), /Unsupported/);
});
test("specialized MCP authorization is not guessed or autoapproved", () => {
  assert.deepEqual(plain(approvalResponse("mcpServer/elicitation/request", {}, false)), { action: "decline", content: null, _meta: null });
  assert.throws(() => approvalResponse("mcpServer/elicitation/request", {}, true), /cannot be approved/);
});
test("only native inProgress status counts as an active turn", () => {
  assert.equal(activeTurn([{ id: "a", status: "inProgress" }, { id: "b", status: "completed" }]).id, "a");
  assert.equal(activeTurn([{ id: "a", status: "failed" }]), undefined);
});

test("worker requests remain visible while worker transcript stays separate", () => {
  assert.equal(routeMessage({ id: 7, method: "item/commandExecution/requestApproval", params: { threadId: "worker" } }, "parent"), "request");
  assert.equal(routeMessage({ method: "serverRequest/resolved", params: { threadId: "worker", requestId: 7 } }, "parent"), "resolved");
  assert.equal(routeMessage({ method: "item/agentMessage/delta", params: { threadId: "worker" } }, "parent"), "ignore");
  assert.equal(routeMessage({ method: "turn/completed", params: { threadId: "parent" } }, "parent"), "notification");
});
test("new threads have usable labels without truncating message storage", () => {
  assert.equal(threadLabel({}), "New conversation");
  assert.equal(threadLabel({ name: "  ", preview: "hello\nworld" }), "hello world");
  const thread = { preview: "x".repeat(50000) };
  assert.equal(threadLabel(thread).length, 96);
  assert.equal(thread.preview.length, 50000);
});
test("simultaneous worker questions cannot share another request's answer", () => {
  assert.notEqual(answerKey(7, "choice"), answerKey(8, "choice"));
  assert.notEqual(answerKey(7, "choice"), answerKey("7", "choice"));
  assert.notEqual(answerKey("a:b", "c"), answerKey("a", "b:c"));
});
test("resume presents interrupted and failed work honestly", () => {
  assert.match(resumeStatus([{ id: "a", status: "interrupted", items: [] }]), /Interrupted.*may not be saved.*No actions replayed/);
  assert.match(resumeStatus([{ id: "a", status: "failed", items: [] }]), /not a completed task/);
  assert.match(resumeStatus([{ id: "a", status: "inProgress", items: [] }]), /still in progress/);
  assert.equal(resumeStatus([]), "Conversation resumed; no tool actions replayed");
});
test("native MCP action confirmation grants once, never copies persistent metadata", () => {
  const params = { serverName: "synthetic", mode: "form", message: "Allow this specific action?",
    requestedSchema: { type: "object", properties: {} },
    _meta: { codex_approval_kind: "mcp_tool_call", persist: ["session", "always"], tool_params: { url: "https://example.com/" } } };
  assert.equal(supportsApproval("mcpServer/elicitation/request", params), true);
  assert.deepEqual(plain(approvalResponse("mcpServer/elicitation/request", params, true)), { action: "accept", content: {}, _meta: null });
  assert.deepEqual(plain(approvalResponse("mcpServer/elicitation/request", params, false)), { action: "decline", content: null, _meta: null });
  for (const override of [
    { mode: "url" }, { mode: "openai/userVerification" }, { _meta: { codex_approval_kind: "browser_auth" } },
    { requestedSchema: { type: "object", properties: { secret: { type: "string" } } } },
    { requestedSchema: { type: "object", properties: {}, required: ["missing"] } }, { serverName: "" },
  ]) {
    const unsupported = { ...params, ...override };
    assert.equal(supportsApproval("mcpServer/elicitation/request", unsupported), false);
    assert.throws(() => approvalResponse("mcpServer/elicitation/request", unsupported, true), /cannot be approved/);
  }
});
