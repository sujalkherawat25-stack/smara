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

test("log lines become rows, newest first, with errors flagged", () => {
  const rows = v.parseLog([
    "2026-11-05 01:50:56 Smara Browser Bridge is on for Demo Shop. Browser opens when Smara needs it. Press Ctrl+C to stop.",
    String.raw`2026-11-05 01:51:10 opened C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe`,
    "2026-11-05 01:52:00 helper crashed, restarting:",
  ]);
  assert.equal(rows.length, 3);
  assert.equal(rows[0].level, "error");
  assert.equal(rows[0].time, "01:52:00");
  assert.equal(rows[1].level, "info");
  assert.match(v.friendlyLog(rows[1].message), /Opened your browser \(brave\.exe\)/);
  assert.doesNotMatch(v.friendlyLog(rows[2].message + ""), /Press Ctrl/);
  assert.doesNotMatch(v.friendlyLog(v.parseLog(["2026-11-05 01:50:56 Smara Browser Bridge is on for Demo Shop. Press Ctrl+C to stop."])[0].message), /Ctrl\+C/);
});

test("connection state: paired and running is on, a trailing error is a problem", () => {
  const ok = ["2026-11-05 01:50:56 Smara Browser Bridge is on for Demo Shop."];
  assert.equal(v.connection({ paired: false, running: false, log: [] }), "unpaired");
  assert.equal(v.connection({ paired: true, running: false, log: ok }), "stopped");
  assert.equal(v.connection({ paired: true, running: true, log: ok }), "running");
  assert.equal(v.connection({ paired: true, running: true, log: [...ok, "2026-11-05 02:00:00 helper crashed, restarting:"] }), "problem");
  assert.equal(v.connection({ paired: true, running: true, log: [] }), "running");
});

test("missing Python, components or browser are named plainly, in the order they must be fixed", () => {
  const all = { python: true, modules: true, browsers: { brave: true, chrome: false, edge: false } };
  assert.deepEqual(JSON.parse(JSON.stringify(v.missingComponents(all))), []);
  assert.deepEqual(JSON.parse(JSON.stringify(v.missingComponents({ ...all, python: false, modules: false }))), ["python"]);
  assert.deepEqual(JSON.parse(JSON.stringify(v.missingComponents({ ...all, modules: false, browsers: { brave: false, chrome: false, edge: false } }))), ["modules", "browser"]);
  assert.match(v.missingText(["python"]), /python\.org/);
  assert.match(v.missingText(["modules"]), /one-click/);
  assert.doesNotMatch(v.missingText(["modules", "browser"]), /terminal|pip|command/i);
  assert.equal(v.missingText([]), "");
});

test("ensure runs automatically only for a paired, stopped helper with nothing missing, and not in a loop", () => {
  const st = (over = {}) => ({ paired: true, running: false, ...over });
  assert.equal(v.shouldEnsure(st(), [], 0, 100_000), true);
  assert.equal(v.shouldEnsure(st({ paired: false }), [], 0, 100_000), false);
  assert.equal(v.shouldEnsure(st({ running: true }), [], 0, 100_000), false);
  assert.equal(v.shouldEnsure(st(), ["modules"], 0, 100_000), false);
  assert.equal(v.shouldEnsure(st(), [], 90_000, 100_000), false);        // tried 10 s ago
  assert.equal(v.shouldEnsure(st(), [], 60_000, 100_000), true);         // 40 s ago
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
