import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";
const load = file => {
  const module = { exports: {} };
  const code = ts.transpileModule(readFileSync(new URL(`../src/${file}`, import.meta.url), "utf8"), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
  vm.runInNewContext(code, { module, exports: module.exports, require: () => ({}) });
  return module.exports;
};
const { jobNotices } = load("jobNotices.ts");
const goal = (id, state, extra = {}) => ({ id, state, detail: "", spec: { agent_id: "kiran", objective: "Total the October sales", ...(extra.spec || {}) }, checkpoint: extra.checkpoint || {}, ...extra });
const name = id => (id === "kiran" ? "Kiran" : "Smara");
const plain = value => JSON.parse(JSON.stringify(value));

test("the first look only sets a baseline, so opening the app does not announce old work", () => {
  const out = jobNotices(null, [goal("a", "ready_for_review"), goal("b", "running")], name);
  assert.deepEqual(plain(out.notices), []);
  assert.deepEqual(plain(out.states), { a: "ready_for_review", b: "running" });
});

test("a job that was working and is now done is announced once with its summary", () => {
  const first = jobNotices(null, [goal("a", "running")], name);
  const done = jobNotices(first.states, [goal("a", "ready_for_review", { checkpoint: { summary: "Total sales 4,52,300 for October" } })], name);
  assert.equal(done.notices.length, 1);
  assert.equal(done.notices[0].text, "Kiran finished: Total sales 4,52,300 for October");
  assert.equal(done.notices[0].tone, "done");
  assert.deepEqual(plain(jobNotices(done.states, [goal("a", "ready_for_review")], name).notices), []);      // the same state is not announced again
});

test("blocked and paused jobs ask for attention; stopped and quiet watches say nothing", () => {
  const before = { a: "running", b: "running", c: "running", d: "queued" };
  const out = jobNotices(before, [goal("a", "blocked", { detail: "The portal needs a CAPTCHA" }), goal("b", "paused", { detail: "Budget reached" }),
    goal("c", "stopped"), goal("d", "completed", { checkpoint: { summary: "NOTHING_NEW since 9:00" } })], name);
  assert.deepEqual(plain(out.notices.map(n => [n.tone, n.text])), [["attention", "Kiran needs attention: The portal needs a CAPTCHA"], ["attention", "Kiran's job paused: Budget reached"]]);
});

test("a long summary is shortened to one line and a job without a summary falls back to its task", () => {
  const long = "word ".repeat(80);
  const a = jobNotices({ a: "running" }, [goal("a", "completed", { checkpoint: { summary: long } })], name).notices[0].text;
  assert.ok(a.length < 170 && a.endsWith("…"));
  assert.equal(jobNotices({ a: "running" }, [goal("a", "completed")], name).notices[0].text, "Kiran finished: Total the October sales");
});

test("a job that appears already finished (never seen working) is not announced", () => {
  assert.deepEqual(plain(jobNotices({}, [goal("new", "completed")], name).notices), []);
});
