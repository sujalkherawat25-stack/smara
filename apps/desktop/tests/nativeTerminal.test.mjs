import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";

const module = { exports: {} };
const source = readFileSync(new URL("../src/terminalProtocol.ts", import.meta.url), "utf8");
const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
vm.runInNewContext(code, { module, exports: module.exports, TextEncoder, TextDecoder, Uint8Array, btoa, atob });
const { appendTerminalOutput, decodeTerminalOutput, encodeTerminalInput, terminalExecParams, TERMINAL_DISPLAY_LIMIT } = module.exports;

test("terminal process is fixed to the selected workspace with network disabled", () => {
  const params = terminalExecParams("C:/project", "terminal-1");
  assert.deepEqual(Array.from(params.command), ["powershell.exe", "-NoLogo", "-NoProfile", "-NoExit", "-Command",
    "if (Get-Module PSReadLine) { Remove-Module PSReadLine -ErrorAction Stop }"]);
  assert.equal(params.tty, true);
  assert.equal(params.disableTimeout, true);
  assert.deepEqual(Array.from(params.sandboxPolicy.writableRoots), ["C:/project"]);
  assert.equal(params.sandboxPolicy.type, "workspaceWrite");
  assert.equal(params.sandboxPolicy.networkAccess, false);
  assert.equal(params.cwd, "C:/project");
});

test("frontend shell startup agrees with the native fixed-command guard", () => {
  const native = readFileSync(new URL("../src-tauri/src/native_runtime.rs", import.meta.url), "utf8");
  const params = terminalExecParams("C:/project", "terminal-1");
  assert.ok(native.includes(JSON.stringify(params.command[5])));
  assert.ok(native.includes('params["command"] != serde_json::json!'));
  assert.equal(params.env, undefined);
});

test("terminal input and streamed UTF-8 output preserve unicode and strip terminal controls", () => {
  const input = atob(encodeTerminalInput("echo नमस्ते\r"));
  assert.equal(new TextDecoder().decode(Uint8Array.from(input, character => character.charCodeAt(0))), "echo नमस्ते\r");
  const decoder = new TextDecoder();
  const bytes = new TextEncoder().encode("ready ✓\u001b[32m green\u001b[0m\r\n");
  const first = btoa(String.fromCharCode(...bytes.slice(0, 8)));
  const second = btoa(String.fromCharCode(...bytes.slice(8)));
  assert.equal(decodeTerminalOutput(first, decoder), "ready ");
  assert.equal(decodeTerminalOutput(second, decoder), "✓ green\n");
});

test("terminal transcript retains only a bounded tail and marks truncation", () => {
  const session = { id: "terminal-1", status: "running", output: "old", truncated: false };
  const next = appendTerminalOutput(session, "x".repeat(TERMINAL_DISPLAY_LIMIT + 3));
  assert.equal(next.output.length, TERMINAL_DISPLAY_LIMIT);
  assert.equal(next.truncated, true);
  assert.equal(session.output, "old");
});
