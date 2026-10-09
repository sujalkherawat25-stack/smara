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
const { displayItem, mergeItem, approvalResponse, activeTurn, routeMessage, answerKey, threadLabel, resumeStatus, supportsApproval } = module.exports;
const plain = value => JSON.parse(JSON.stringify(value));

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
