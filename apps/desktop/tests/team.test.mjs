import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";
const module = { exports: {} };
const code = ts.transpileModule(readFileSync(new URL("../src/team.ts", import.meta.url), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
vm.runInNewContext(code, { module, exports: module.exports });
const t = module.exports;
const goal = (agent, state, objective, sandbox = "read-only", lease = null) => ({ id: objective, state, lease, spec: { agent_id: agent, objective, sandbox } });

test("Smara is the manager and is listed first; everyone else is the team", () => {
  const roster = { roster: [{ id: "kiran", name: "Kiran" }, { id: "smara", name: "Munshi" }, { id: "meera", name: "Meera" }], default: "smara", assignments: {} };
  const { manager, employees } = t.splitRoster(roster);
  assert.equal(manager.name, "Munshi");
  assert.equal(JSON.stringify(employees.map(e => e.id)), JSON.stringify(["kiran", "meera"]));
});

test("status says who needs the owner first, then who is working, then the last chat", () => {
  const goals = [goal("meera", "running", "Sort today's inbox"), goal("kiran", "awaiting_approval", "Post the day book"), goal("rohan", "completed", "Old job")];
  assert.equal(t.memberStatus("kiran", goals, "Totals", "Accounts").tone, "needs");
  assert.match(t.memberStatus("kiran", goals, "", "").text, /^Needs you · Post the day book/);
  assert.equal(t.memberStatus("meera", goals, "", "").text, "Working · Sort today's inbox");
  assert.equal(t.memberStatus("rohan", goals, "Reminder for Sharma", "Dues").text, "Reminder for Sharma");
  assert.equal(t.memberStatus("nisha", goals, "", "Documents").text, "Ready for work");
  assert.equal(t.memberStatus("smara", [goal("", "queued", "Plan the week")], "", "").text, "Up next · Plan the week");
});

test("only a task that edits project files keeps the chat from sending", () => {
  assert.equal(t.blocksChat([goal("meera", "running", "Read mail", "read-only", "lease")]), false);
  assert.equal(t.blocksChat([goal("kiran", "running", "Fix the sheet", "workspace-write", "lease")]), true);
  assert.equal(t.blocksChat([goal("kiran", "queued", "Fix the sheet", "workspace-write", null)]), false);
});

test("job text and standing rules split the same way the team tool saves them", () => {
  const { job, rules } = t.splitJob("You read the inbox.\n\nStanding rules from the owner:\n- Always cc me.\n- Never reply to spam.");
  assert.equal(job, "You read the inbox.");
  assert.equal(JSON.stringify(rules), JSON.stringify(["Always cc me.", "Never reply to spam."]));
  assert.equal(t.splitJob("Just a job").rules.length, 0);
  const python = readFileSync(new URL("../../../src/smara/native_team.py", import.meta.url), "utf8");
  assert.match(python, new RegExp(`RULES_HEADING = "${t.RULES_HEADING}"`));
  assert.equal(t.initials("Meera Shah"), "MS");
  assert.equal(t.initials("kiran"), "K");
});

test("Hire opens Smara's chat with the starter text, so the job is explained in chat", () => {
  const app = readFileSync(new URL("../src/NativeApp.tsx", import.meta.url), "utf8");
  assert.match(app, /const hireInChat = \(\) => \{\n\s+setSelectedAgent\(MANAGER_ID\)/);
  assert.match(app, /setDraft\(HIRE_STARTER\)/);
  assert.match(app, />\+ Hire<\/button>/);
  assert.equal(t.HIRE_STARTER, "I want to hire someone to ");
});

test("routines are listed under their own employee, with a plain status line", () => {
  const mine = { id: "a", name: "Kiran: Check Tally for unpaid invoices", enabled: 1, runs: 2, max_runs: 30, due: 0, interval: 86400, spec: { agent_id: "kiran" }, goal_state: "completed" };
  const other = { ...mine, id: "b", spec: { agent_id: "meera" } };
  const manager = { ...mine, id: "c", spec: {} };
  assert.deepEqual(t.routinesFor([mine, other, manager], "kiran").map(r => r.id), ["a"]);
  assert.deepEqual(t.routinesFor([mine, other, manager], "smara").map(r => r.id), ["c"]);
  assert.match(t.routineLine(mine), /^Check Tally for unpaid invoices · every day · next .* · 2\/30 runs$/);
  assert.match(t.routineLine({ ...mine, enabled: 0 }), /paused/);
  assert.match(t.routineLine({ ...mine, goal_state: "awaiting_acceptance" }), /waiting for you to accept the last report/);
});
