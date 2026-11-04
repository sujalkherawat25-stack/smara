import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";
const module = { exports: {} };
const code = ts.transpileModule(readFileSync(new URL("../src/browserBridgeView.ts", import.meta.url), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
vm.runInNewContext(code, { module, exports: module.exports });
const v = module.exports;
const base = { paired: true, running: true, paused: false, last_poll_ok_ms: 100_000 };

test("online light follows the server's 45 second window", () => {
  assert.equal(v.connection({ ...base, paired: false }, 1), "unpaired");
  assert.equal(v.connection({ ...base, running: false }, 1), "stopped");
  assert.equal(v.connection({ ...base, last_poll_ok_ms: 0 }, 1), "starting");
  assert.equal(v.connection(base, 100_000 + 44_000), "online");
  assert.equal(v.connection(base, 100_000 + 46_000), "offline");
  assert.equal(v.connection({ ...base, paused: true }, 100_001), "paused");
});

test("steps pair with results and failures stay visible", () => {
  const rows = v.buildFeed([
    { kind: "started", ts: 1 },
    { kind: "step", ts: 2, job_id: "a", action: "navigate", host: "example.com", url: "https://example.com/x" },
    { kind: "result", ts: 3, job_id: "a", ok: true, title: "Example", host: "example.com", ms: 1200 },
    { kind: "step", ts: 4, job_id: "b", action: "fill", selector: "#q", value: "shoes" },
    { kind: "result", ts: 5, job_id: "b", ok: false, error: "I never type into password fields" },
    { kind: "step", ts: 6, job_id: "c", action: "click", selector: "#buy" },
  ]);
  assert.equal(rows.length, 3);
  assert.deepEqual(JSON.parse(JSON.stringify(rows.map(r => [r.label, r.status]))),
    [["Open example.com", "ok"], ["Type into #q “shoes”", "error"], ["Click #buy", "running"]]);
  assert.equal(rows[0].title, "Example");
  assert.match(rows[1].error, /never type/);
  assert.equal(v.settleFeed(rows, true)[2].status, "running");
  assert.equal(v.settleFeed(rows, false)[2].status, "error");
});

test("heartbeat polls stay out of the feed but poll failures and exits show", () => {
  let events = [];
  events = v.mergeEvents(events, { kind: "poll", ts: 1, ok: true });
  assert.equal(events.length, 0);
  events = v.mergeEvents(events, { kind: "poll", ts: 2, ok: false, status: 502 });
  events = v.mergeEvents(events, { kind: "exited", ts: 3, code: 1 });
  const rows = v.buildFeed(events);
  assert.match(rows[0].label, /HTTP 502/);
  assert.match(rows[1].label, /code 1/);
});

test("feed and event buffers are bounded", () => {
  let events = [];
  for (let i = 0; i < 1000; i++) events = v.mergeEvents(events, { kind: "step", ts: i, job_id: `j${i}`, action: "read" });
  assert.ok(events.length <= v.MAX_FEED * 2);
  assert.ok(v.buildFeed(events).length <= v.MAX_FEED);
});

test("autostart fires once, only when paired and idle", () => {
  const s = (over = {}) => ({ paired: true, running: false, config: { autostart: true }, ...over });
  assert.equal(v.shouldAutoStart(s(), false, false), true);
  assert.equal(v.shouldAutoStart(s({ paired: false }), false, false), false);
  assert.equal(v.shouldAutoStart(s({ running: true }), false, false), false);
  assert.equal(v.shouldAutoStart(s({ config: { autostart: false } }), false, false), false);
  assert.equal(v.shouldAutoStart(s(), true, false), false);
  assert.equal(v.shouldAutoStart(s(), false, true), false);
});

test("pairing input is validated before anything is sent", () => {
  assert.equal(v.validatePairInput("https://ai.syntarus.com/munim", "abcd-efgh"), "");
  assert.equal(v.validatePairInput("http://localhost:8000", "ABCD EFGH"), "");
  assert.match(v.validatePairInput("http://evil.example", "ABCD-EFGH"), /https/);
  assert.match(v.validatePairInput("ai.syntarus.com", "ABCD-EFGH"), /full server address/);
  assert.match(v.validatePairInput("https://ai.syntarus.com/munim", "123"), /pairing code/);
});

test("approval cards: first answer wins and the site grant lasts 15 minutes", () => {
  const card = { id: "s1", page: "https://shop.example/cart", host: "shop.example", element: "#add", value: "", action: "click", asked_at: 1000 };
  const fill = { ...card, id: "s2", action: "fill", element: "#qty", value: "3" };
  assert.deepEqual(JSON.parse(JSON.stringify(v.APPROVAL_BUTTONS.map(b => b.choice))), ["approve", "site", "reject"]);
  assert.equal(v.pendingApprovals([card, fill], new Set(["s1"]), 2000).length, 1);
  assert.equal(v.pendingApprovals([card], new Set(), 1000 + 25 * 3600_000).length, 0);
  assert.equal(v.siteGrantUntil(0), 15 * 60_000);
  assert.match(v.describeApproval(fill), /type into #qty on shop.example — “3”/);
  assert.equal(v.approvalOutcome(200), "done");
  assert.equal(v.approvalOutcome(409), "already");
  assert.equal(v.approvalOutcome(404), "already");
  assert.equal(v.approvalOutcome(405), "unsupported");
  assert.equal(v.approvalOutcome(500), "error");
});
