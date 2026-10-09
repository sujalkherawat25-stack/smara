import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";
const module = { exports: {} };
const code = ts.transpileModule(readFileSync(new URL("../src/nativeApi.ts", import.meta.url), "utf8"), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
vm.runInNewContext(code, { module, exports: module.exports, require: () => ({ invoke: () => assert.fail("No RPC during path comparison") }) });
const { sameWorkspace } = module.exports;
test("canonical Windows project identity tolerates native extended prefix, not siblings", () => {
  assert.equal(sameWorkspace("C:\\Code\\Smara", "\\\\?\\c:\\code\\smara\\"), true);
  assert.equal(sameWorkspace("C:\\Code\\Smara", "C:\\Code\\Smara-other"), false);
  assert.equal(sameWorkspace("C:\\Code\\Smara", "C:\\Code\\Smara\\child"), false);
  assert.equal(sameWorkspace("", ""), false);
  assert.equal(sameWorkspace("/code/Smara", "/code/smara"), false);
});
test("shipping UI imports no legacy app or broad tool bridge", () => {
  const app = readFileSync(new URL("../src/NativeApp.tsx", import.meta.url), "utf8");
  assert.doesNotMatch(app, /LegacyApp|from "\.\/App"|from "\.\/api"/);
  assert.match(app, /sameWorkspace\(saved\.thread\.cwd, workspace\)/);
  assert.match(app, /setToolsEnabled\(false\); setWorkersEnabled\(false\); setBrowserOrigins\(""\)/);
});
