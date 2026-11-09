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
const agents = load("../src/agents.ts");
const hosted = load("../src/hostedStatus.ts");
const plain = (value) => JSON.parse(JSON.stringify(value));

const agent = (id, name) => ({ id, name, role: "", icon: "🤖", instructions: "", preset: true, folders: [], origins: [], terminal: false, memory_scope: "project", model_profile: "" });
const roster = { roster: [agent("kiran", "Kiran"), agent("rohan", "Rohan"), agent("meena", "Meena")], default: "kiran", assignments: { "t-1": "rohan", "t-2": "gone", "t-3": "meena" } };

test("a chat belongs to its assigned agent, and to the default agent when it has none or its agent is gone", () => {
  assert.equal(agents.agentOfChat(roster, "t-1"), "rohan");
  assert.equal(agents.agentOfChat(roster, "t-9"), "kiran");
  assert.equal(agents.agentOfChat(roster, "t-2"), "kiran");
});

test("the chat list filters by agent and counts add up to every chat", () => {
  const chats = [{ id: "t-1" }, { id: "t-2" }, { id: "t-3" }, { id: "t-9" }];
  assert.deepEqual(plain(agents.chatsForAgent(chats, roster, "kiran")).map(chat => chat.id), ["t-2", "t-9"]);
  assert.deepEqual(plain(agents.chatCounts(chats, roster)), { rohan: 1, kiran: 2, meena: 1 });
});

test("agent names must be present and under 40 characters", () => {
  assert.match(agents.agentNameProblem("   "), /Give the agent a name/);
  assert.match(agents.agentNameProblem("x".repeat(41)), /under 40/);
  assert.equal(agents.agentNameProblem("Meena"), "");
});

test("the hosted chip says connected, paused, offline or not paired, in plain words", () => {
  assert.deepEqual(plain(hosted.hostedStatus(undefined)), { label: "Hosted: checking…", tone: "off" });
  assert.deepEqual(plain(hosted.hostedStatus({ paired: false })), { label: "Hosted: not paired", tone: "off" });
  assert.deepEqual(plain(hosted.hostedStatus({ paired: true, paused: true, shop: "Ravi Traders" })), { label: "Hosted: paused", tone: "warn" });
  assert.deepEqual(plain(hosted.hostedStatus({ paired: true, running: true, shop: "Ravi Traders" })), { label: "Hosted: connected to Ravi Traders", tone: "ok" });
  assert.deepEqual(plain(hosted.hostedStatus({ paired: true, running: false })), { label: "Hosted: offline", tone: "warn" });
});
