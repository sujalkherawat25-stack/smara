import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";

const module = { exports: {} };
const source = readFileSync(new URL("../src/nativeArtifacts.ts", import.meta.url), "utf8");
const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
vm.runInNewContext(code, { module, exports: module.exports });
const { previewableLocalPath } = module.exports;

test("recognizes workspace artifact paths in code spans", () => {
  assert.equal(previewableLocalPath("reports/latest.md"), "reports/latest.md");
  assert.equal(previewableLocalPath("C:\\work\\out\\chart.png"), "C:\\work\\out\\chart.png");
  assert.equal(previewableLocalPath("'database.csv'"), "database.csv");
});

test("does not offer local previews for URLs or executable files", () => {
  assert.equal(previewableLocalPath("https://example.com/report.pdf"), null);
  assert.equal(previewableLocalPath("setup.exe"), null);
  assert.equal(previewableLocalPath("\\\\server\\share\\report.pdf"), null);
  assert.equal(previewableLocalPath("snippet.ts\nmalicious"), null);
});
