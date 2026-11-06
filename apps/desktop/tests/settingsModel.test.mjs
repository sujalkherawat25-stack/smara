import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";

const module = { exports: {} };
vm.runInNewContext(ts.transpileModule(readFileSync(new URL("../src/settingsModel.ts", import.meta.url), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText, { module, exports: module.exports });
const { modelPayload, profileId, browsesAnySite, ANY_SITE } = module.exports;

test("a blank context window is left out; a number is sent as a number", () => {
  assert.equal("context_window" in modelPayload({ id: "a", context_window: "" }), false);
  assert.equal(modelPayload({ id: "a", context_window: "200000" }).context_window, 200000);
});

test("the profile ID is made from the display name and never collides", () => {
  assert.equal(profileId("Sarvam GLM 5.3", []), "sarvam_glm_5_3");
  assert.equal(profileId("Sarvam GLM 5.3", ["sarvam_glm_5_3"]), "sarvam_glm_5_3_2");
  assert.equal(profileId("  ", []), "model");
});

test("browsing any public site is the single star, alone or beside other sites", () => {
  assert.equal(ANY_SITE, "*");
  assert.equal(browsesAnySite("*"), true);
  assert.equal(browsesAnySite("https://a.example *"), true);
  assert.equal(browsesAnySite(""), false);
  assert.equal(browsesAnySite("https://a.example"), false);
});
