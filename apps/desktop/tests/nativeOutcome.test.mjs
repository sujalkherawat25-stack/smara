import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";

function render(outcome, onPreviewFile) {
  const source = readFileSync(new URL("../src/NativeOutcome.tsx", import.meta.url), "utf8");
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  const module = { exports: {} };
  vm.runInNewContext(code, { module, exports: module.exports, require(name) {
    if (name === "react/jsx-runtime") return { jsx: (type, props) => ({ type, props }), jsxs: (type, props) => ({ type, props }) };
    if (name === "./NativeMarkdown") return { default: () => null };
    assert.fail(name);
  } });
  const node = module.exports.default({ goal: { checkpoint: { outcome } }, onPreviewFile });
  const text = value => Array.isArray(value) ? value.map(text).join("") : value && typeof value === "object" ? text(value.props?.children) : String(value ?? "");
  return { node, text: text(node) };
}

test("result scorecard does not represent unknown coverage as a pass", () => {
  const ui = render({ passed: false, checks: [], elapsed_seconds: 2, sources: [], failed_pages: ["Synthetic failed page"], claims: [], metrics: { citation_support: null, evidence_coverage: null } });
  assert.match(ui.text, /Not measured/);
  assert.match(ui.text, /Gaps remain/);
  assert.match(ui.text, /not semantic correctness/);
  assert.match(ui.text, /Synthetic failed page/);
});

test("recorded outputs preview current project files without claiming hashes are current", () => {
  const paths = [];
  const ui = render({ passed: true, checks: [], elapsed_seconds: 2, sources: [], failed_pages: [], claims: [],
    outputs: [{ path: "report.html", bytes: 120, sha256: "synthetic-hash" }] }, path => paths.push(path));
  const visit = value => Array.isArray(value) ? value.flatMap(visit) : value && typeof value === "object" ? [value, ...visit(value.props?.children)] : [];
  const button = visit(ui.node).find(node => node.type === "button");
  button.props.onClick();
  assert.deepEqual(paths, ["report.html"]);
  assert.match(ui.text, /verification time/);
  assert.match(ui.text, /previews read the current project file/);
  assert.match(ui.text, /synthetic-hash/);
  assert.match(ui.text, /review required/);
});

test("no outcome means no manufactured scorecard", () => {
  assert.equal(render(undefined).node, null);
});
