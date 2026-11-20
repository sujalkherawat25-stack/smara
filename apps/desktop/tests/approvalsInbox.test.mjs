import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";
const module = { exports: {} };
const code = ts.transpileModule(readFileSync(new URL("../src/approvalsView.ts", import.meta.url), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
vm.runInNewContext(code, { module, exports: module.exports });
const v = module.exports;

test("answers read as what happened, and first-answer-wins is explained", () => {
  assert.equal(v.outcomeText("done", "approve"), "Approved. Smara carries on.");
  assert.equal(v.outcomeText("done", "site"), "Approved, and this site is allowed for 15 minutes.");
  assert.equal(v.outcomeText("done", "reject"), "Rejected. Smara will not do it.");
  assert.match(v.outcomeText("already", "approve"), /Already answered/);
  assert.match(v.outcomeText("chat_only", "approve"), /in chat only/);
});

test("labels, ages, counts and the code format", () => {
  assert.equal(v.toolLabel("browser_step"), "Browser on this PC");
  assert.equal(v.toolLabel("pc_files"), "Smara Shared folder");
  const now = Date.parse("2026-11-18T10:10:00Z");
  assert.equal(v.askedLabel("2026-11-18T10:09:30Z", now), "just now");
  assert.equal(v.askedLabel("2026-11-18T10:00:00Z", now), "10 min ago");
  assert.equal(v.askedLabel(null, now), "");
  assert.equal(v.waitingCount(2, 1, 0), 3);
  assert.equal(v.codeLooksRight("ABCD-EFGH"), true);
  assert.equal(v.codeLooksRight("abcdefgh"), true);
  assert.equal(v.codeLooksRight("ABC-DEFG"), false);
});

test("the inbox answers through the owner's approvals link and asks before disconnecting", () => {
  const page = readFileSync(new URL("../src/NativeApprovals.tsx", import.meta.url), "utf8");
  assert.match(page, /operation: "hosted_approvals"/);
  assert.match(page, /hosted<\{ outcome: Outcome \}>\("decide", \{ id, choice \}\)/);
  assert.match(page, /window\.confirm\("Stop answering Smara's approvals on this PC\?/);
  assert.match(page, /goalRequest\("respond", \{ id: goal\.id, request_id: request\.id, allow: true \}\)/);
  const app = readFileSync(new URL("../src/NativeApp.tsx", import.meta.url), "utf8");
  assert.match(app, /panel === "inbox" && <NativeApprovals/);
  const rust = readFileSync(new URL("../src-tauri/src/native_main.rs", import.meta.url), "utf8");
  assert.match(rust, /"hosted_approvals", "hosted_memory"/);
});

test("memory sharing is optional, off until chosen, and the page asks before erasing", () => {
  assert.match(v.sharingText({ connected: false, shop: "", enabled: false }), /Connect this PC/);
  assert.match(v.sharingText({ connected: true, shop: "Sharma Traders", enabled: false }), /^Off\. Nothing/);
  assert.match(v.sharingText({ connected: true, shop: "Sharma Traders", enabled: true }), /On for Sharma Traders.*Passwords, keys, card numbers and OTPs are removed/);
  const page = readFileSync(new URL("../src/NativeApprovals.tsx", import.meta.url), "utf8");
  assert.match(page, /operation: "hosted_memory"/);
  assert.match(page, /sharing<SharingStatus>\("set", \{ enabled: event\.target\.checked \}\)/);
  assert.match(page, /window\.confirm\("Erase everything this PC shared with Smara\?/);
  const rust = readFileSync(new URL("../src-tauri/src/native_main.rs", import.meta.url), "utf8");
  assert.match(rust, /"hosted_memory"/);
});
