import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";
const module = { exports: {} };
const code = ts.transpileModule(readFileSync(new URL("../src/auditView.ts", import.meta.url), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
vm.runInNewContext(code, { module, exports: module.exports });
const v = module.exports;
const row = (over) => ({ id: "1", ts: 1763460000, source: "desktop", task: "t", kind: "browser", action: "browser_click",
  target: "element=Download statement", detail: null, outcome: "completed ok", approval: "", screenshot: "", ...over });

test("steps read as plain words and say who did them", () => {
  assert.equal(v.actionLabel(row()), "Clicked");
  assert.equal(v.actionLabel(row({ action: "download" })), "Downloaded a file");
  assert.equal(v.actionLabel(row({ action: "something_new" })), "something new");
  assert.equal(v.whoLabel(row()), "Smara Desktop");
  assert.equal(v.whoLabel(row({ source: "bridge" })), "Smara (Telegram)");
  assert.equal(v.whoLabel(row({ kind: "approval", action: "approved" })), "You");
});

test("failures are flagged and filters combine kind and words", () => {
  assert.equal(v.failed(row({ outcome: "error: the owner paused Smara" })), true);
  assert.equal(v.failed(row({ outcome: "failed timeout" })), true);
  assert.equal(v.failed(row()), false);
  const rows = [row(), row({ id: "2", kind: "computer", action: "computer_act", target: "window_id=42" }),
    row({ id: "3", kind: "approval", action: "declined", target: "playwright" })];
  assert.deepEqual(v.filterEntries(rows, "all", "").map(r => r.id), ["1", "2", "3"]);
  assert.deepEqual(v.filterEntries(rows, "computer", "").map(r => r.id), ["2"]);
  assert.deepEqual(v.filterEntries(rows, "all", "clicked statement").map(r => r.id), ["1"]);
  assert.deepEqual(v.filterEntries(rows, "all", "you declined").map(r => r.id), ["3"]);
});

test("sizes and times are short", () => {
  assert.equal(v.sizeLabel(1_500_000), "1.5 MB");
  assert.equal(v.sizeLabel(200), "1 KB");
  assert.match(v.timeLabel(1763460000), /^\d\d:\d\d:\d\d$/);
});

test("the Activity log page is in Settings and asks before deleting", () => {
  const settings = readFileSync(new URL("../src/NativeSettings.tsx", import.meta.url), "utf8");
  assert.match(settings, /id: "activity", label: "Activity log"/);
  const page = readFileSync(new URL("../src/NativeAudit.tsx", import.meta.url), "utf8");
  assert.match(page, /window\.confirm\(`Delete \$\{what\}\? This cannot be undone\.`\)/);
  assert.match(page, /operation: "audit"/);
});
