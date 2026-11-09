import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";

const module = { exports: {} };
const code = ts.transpileModule(readFileSync(new URL("../src/nativeSettingsPolicy.ts", import.meta.url), "utf8"), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
vm.runInNewContext(code, { module, exports: module.exports });
const { runtimeRestartNeeded } = module.exports;

const running = { profileId: "local:glm", credentialName: "GLM_KEY" };

test("nothing restarts the runtime when it is not running", () => {
  assert.equal(runtimeRestartNeeded("select_model", { id: "local:other" }, null), false);
  assert.equal(runtimeRestartNeeded("save_model", { profile: { id: "local:glm" } }, null), false);
});

test("model, project and tool-option changes restart a running runtime", () => {
  assert.equal(runtimeRestartNeeded("select_model", { id: "local:other" }, running), true);
  assert.equal(runtimeRestartNeeded("select_project", { workspace: "C:/work" }, running), true);
  assert.equal(runtimeRestartNeeded("save_project_options", { options: {} }, running), true);
});

test("editing the active model or its key restarts; other models and keys do not", () => {
  assert.equal(runtimeRestartNeeded("save_model", { profile: { id: "local:glm" } }, running), true);
  assert.equal(runtimeRestartNeeded("save_model", { profile: { id: "local:other" } }, running), false);
  assert.equal(runtimeRestartNeeded("save_credential", { name: "GLM_KEY" }, running), true);
  assert.equal(runtimeRestartNeeded("delete_credential", { name: "GLM_KEY" }, running), true);
  assert.equal(runtimeRestartNeeded("save_credential", { name: "TAVILY_API_KEY" }, running), false);
});

test("memory, search, probes, outreach and project add keep the chat connected", () => {
  for (const operation of ["save_memory", "select_search", "test_model", "test_search", "test_memory", "save_outreach", "add_project", "bootstrap"]) {
    assert.equal(runtimeRestartNeeded(operation, {}, running), false, operation);
  }
});
