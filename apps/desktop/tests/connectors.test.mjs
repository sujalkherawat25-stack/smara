import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";
const load = file => {
  const module = { exports: {} };
  const code = ts.transpileModule(readFileSync(new URL(`../src/${file}`, import.meta.url), "utf8"), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
  vm.runInNewContext(code, { URL, module, exports: module.exports, require: name => (name === "./connectors" ? load("connectors.ts") : {}) });
  return module.exports;
};
const c = load("connectors.ts");

test("connector servers are recognised and named for people", () => {
  const a = load("askCard.ts");
  assert.equal(a.isConnectorServer("conn_gmail"), true);
  assert.equal(a.isConnectorServer("smara_team"), false);
  assert.equal(a.connectorLabel("conn_google_sheets"), "Google sheets");
  assert.equal(a.toolWords("mcp__conn_gmail__send_mail"), "send mail");
});

test("the add form explains what is missing", () => {
  const base = { label: "Gmail", kind: "http", url: "https://mail.example.com/mcp", command: "" };
  assert.equal(c.connectorProblem(base), "");
  assert.match(c.connectorProblem({ ...base, label: " " }), /name/);
  assert.match(c.connectorProblem({ ...base, url: "http://evil.example" }), /https/);
  assert.equal(c.connectorProblem({ ...base, url: "http://127.0.0.1:8000/mcp" }), "");
  assert.match(c.connectorProblem({ ...base, kind: "stdio" }), /command/);
  assert.equal(c.connectorProblem({ ...base, kind: "stdio", command: "npx" }), "");
});

test("arguments keep quoted parts together", () => {
  assert.equal(JSON.stringify(c.splitArgs('-y @scope/server --root "C:\My Files"')), JSON.stringify(["-y", "@scope/server", "--root", "C:\My Files"]));
});

test("the list shows the host or the command, never a token", () => {
  assert.equal(c.connectorTarget({ kind: "http", url: "https://mail.example.com/mcp", command: "", args: [] }), "mail.example.com");
  assert.equal(c.connectorTarget({ kind: "stdio", url: "", command: "npx", args: ["-y", "x"] }), "npx -y x");
});

test("a connector tool shows a clear ask card, and sending is flagged", () => {
  const card = load("askCard.ts");
  const ask = (tool, args) => card.askView({ method: "mcpServer/elicitation/request", params: { serverName: "conn_gmail", message: `Allow the conn_gmail MCP server to run tool "${tool}"?`, _meta: { tool_name: tool, tool_params: args } } });
  const read = ask("list_inbox", {});
  assert.equal(read.title, "Let Gmail list inbox?");
  assert.equal(read.risky, false);
  const send = ask("send_mail", { to: "a@b.example", subject: "Hi" });
  assert.equal(send.title, "Let Gmail send mail?");
  assert.equal(send.risky, true);
  assert.match(send.detail, /to: a@b.example/);
});
