import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";
const module = { exports: {} };
const code = ts.transpileModule(readFileSync(new URL("../src/signIn.ts", import.meta.url), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
vm.runInNewContext(code, { module, exports: module.exports });
const s = module.exports;

test("the waiting line tells the owner the one next thing", () => {
  assert.match(s.waitingText("pending"), /Tap a link/);
  assert.match(s.waitingText(undefined), /Tap a link/);
  assert.match(s.waitingText("asked"), /Reply YES/);
  assert.match(s.waitingText("denied"), /nothing was connected/);
  assert.match(s.waitingText("expired"), /new code/);
});

test("minutes left never goes below zero and rounds up", () => {
  assert.equal(s.minutesLeft(1000 + 61, 1000), 2);
  assert.equal(s.minutesLeft(1000 + 600, 1000), 10);
  assert.equal(s.minutesLeft(900, 1000), 0);
});

test("only approved, denied and expired end a sign-in", () => {
  for (const x of ["approved", "denied", "expired"]) assert.equal(s.finished(x), true);
  for (const x of ["pending", "asked", "none", undefined]) assert.equal(s.finished(x), false);
});

test("the connected line follows the helper chip", () => {
  assert.equal(s.connectedLine("Demo Shop", "Hosted: connected to Demo Shop"), "Connected to Demo Shop");
  assert.equal(s.connectedLine("Demo Shop", "Hosted: offline"), "Signed in to Demo Shop · reconnecting");
  assert.equal(s.connectedLine("", "Hosted: paused"), "Signed in to your shop · paused");
});
