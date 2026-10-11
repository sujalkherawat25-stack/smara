// Presentation tests only: no shell command or paid model is executed.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import vm from "node:vm";
import ts from "typescript";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

const module = { exports: {} };
const code = ts.transpileModule(readFileSync(new URL("../src/NativeCommand.tsx", import.meta.url), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
}).outputText;
vm.runInNewContext(code, { module, exports: module.exports, require: createRequire(import.meta.url) });
const render = item => renderToStaticMarkup(createElement(module.exports.default, { item }));

test("native nonzero command exits and denied requests are distinct", () => {
  const failed = render({ id: "a", status: "completed", command: "pytest", exitCode: 1, cwd: "C:/synthetic", durationMs: 2500, text: "one failure" });
  assert.match(failed, /Failed/); assert.match(failed, /Exit 1/); assert.match(failed, /2.5s/); assert.match(failed, /C:\/synthetic/);
  assert.match(render({ id: "b", status: "declined", text: "" }), /Denied/);
  assert.match(render({ id: "c", status: "completed", text: "" }), /Exit code unavailable/);
});

test("long command output is retained and rendered as escaped text", () => {
  const text = "synthetic output\n".repeat(20_000) + "<script>unsafe()</script>END-OF-OUTPUT";
  const html = render({ id: "long", status: "completed", exitCode: 0, command: "synthetic transcript", text });
  assert.equal(html.split("synthetic output").length - 1, 20_000);
  assert.match(html, /&lt;script&gt;unsafe\(\)&lt;\/script&gt;END-OF-OUTPUT/);
  assert.doesNotMatch(html, /<script>/);
});
