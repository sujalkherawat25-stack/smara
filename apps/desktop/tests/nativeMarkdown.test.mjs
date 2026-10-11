import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";

const module = { exports: {} };
const source = readFileSync(new URL("../src/NativeMarkdown.tsx", import.meta.url), "utf8");
const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true } }).outputText;
const elements = { jsx: (type, props) => ({ type, props }), jsxs: (type, props) => ({ type, props }) };
const helperModule = { exports: {} };
const helperSource = readFileSync(new URL("../src/nativeArtifacts.ts", import.meta.url), "utf8");
const helperCode = ts.transpileModule(helperSource, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
vm.runInNewContext(helperCode, { module: helperModule, exports: helperModule.exports });
vm.runInNewContext(code, {
  module,
  exports: module.exports,
  require(name) {
    if (name === "react/jsx-runtime") return elements;
    if (name === "@tauri-apps/api/core") return { invoke: async () => {} };
    if (name === "./nativeArtifacts") return helperModule.exports;
    assert.fail(`Unexpected import ${name}`);
  },
});

function walk(node, output = []) {
  if (Array.isArray(node)) node.forEach(child => walk(child, output));
  else if (node && typeof node === "object") { output.push(node); walk(node.props?.children, output); }
  return output;
}
function label(node) { return Array.isArray(node) ? node.map(label).join("") : node && typeof node === "object" ? label(node.props?.children) : String(node ?? ""); }

test("assistant markdown offers previews for local artifact paths, not executables or URLs", () => {
  const opened = [];
  const rendered = module.exports.default({ text: "See `reports/summary.pdf`, `setup.exe`, and `https://example.com/a.pdf`.", onPreviewFile: path => opened.push(path) });
  const nodes = walk(rendered);
  const previewButtons = nodes.filter(node => node.type === "button" && label(node) === "Preview");
  assert.equal(previewButtons.length, 1);
  previewButtons[0].props.onClick();
  assert.deepEqual(opened, ["reports/summary.pdf"]);
});
