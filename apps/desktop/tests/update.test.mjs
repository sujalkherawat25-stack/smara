import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";
const load = file => {
  const module = { exports: {} };
  const code = ts.transpileModule(readFileSync(new URL(`../src/${file}`, import.meta.url), "utf8"), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
  vm.runInNewContext(code, { module, exports: module.exports });
  return module.exports;
};
const u = load("update.ts");

test("the update bar says what is happening", () => {
  assert.equal(u.updateLine("idle", "0.2.7"), "Smara Desktop 0.2.7 is ready.");
  assert.match(u.updateLine("installing", "0.2.7", 42.4), /\(42%\).*restarts by itself/);
  assert.doesNotMatch(u.updateLine("installing", "0.2.7", 0), /%/);
  assert.match(u.updateLine("failed", "0.2.7"), /did not install/);
});

test("an update never restarts the app in the middle of work", () => {
  assert.equal(u.canInstall("idle", true), false);
  assert.equal(u.canInstall("idle", false), true);
  assert.equal(u.canInstall("failed", false), true);
  assert.equal(u.canInstall("installing", false), false);
});

test("the app checks Smara's own server and only installs signed updates", () => {
  const conf = JSON.parse(readFileSync(new URL("../src-tauri/tauri.conf.json", import.meta.url), "utf8"));
  assert.equal(conf.bundle.createUpdaterArtifacts, true);
  assert.deepEqual(conf.plugins.updater.endpoints, ["https://ai.syntarus.com/smara/desktop/update/{{target}}/{{arch}}/{{current_version}}"]);
  assert.ok(conf.plugins.updater.pubkey.length > 40);
  const caps = JSON.parse(readFileSync(new URL("../src-tauri/capabilities/default.json", import.meta.url), "utf8"));
  assert.ok(caps.permissions.includes("updater:default") && caps.permissions.includes("process:allow-restart"));
});
