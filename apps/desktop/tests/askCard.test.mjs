import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";
const module = { exports: {} };
const code = ts.transpileModule(readFileSync(new URL("../src/askCard.ts", import.meta.url), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
vm.runInNewContext(code, { module, exports: module.exports });
const { askView } = module.exports;

test("a browser step reads as a plain question, and outward steps are marked", () => {
  const view = askView({ method: "mcpServer/elicitation/request", params: { serverName: "playwright",
    message: "Allow the playwright tool browser_click: “Submit return” button on services.gst.gov.in?" } });
  assert.equal(view.title, "Let Smara click in the browser?");
  assert.match(view.summary, /Submit return/);
  assert.equal(view.risky, true);
  const quiet = askView({ method: "mcpServer/elicitation/request", params: { serverName: "playwright", message: "Allow the playwright tool browser_navigate: https://example.com?" } });
  assert.equal(quiet.title, "Let Smara open a page in the browser?");
  assert.equal(quiet.risky, false);
});

test("commands, file changes and access requests each get their own wording", () => {
  const command = askView({ method: "item/commandExecution/requestApproval", params: { command: "python report.py", cwd: "C:/Shop", reason: "Build the report" } });
  assert.equal(command.title, "Run a command on this PC?");
  assert.equal(command.summary, "Build the report");
  assert.match(command.detail, /python report\.py[\s\S]*Folder: C:\/Shop/);
  assert.equal(askView({ method: "item/fileChange/requestApproval", params: {} }).title, "Change files in this project?");
  assert.match(askView({ method: "item/permissions/requestApproval", params: { permissions: { network: {} } } }).summary, /internet access/);
});

test("the card replaces the composer, keeps Stop reachable, and the raw JSON dump is gone", () => {
  const app = readFileSync(new URL("../src/NativeApp.tsx", import.meta.url), "utf8");
  assert.match(app, /approvals\[0\] \? <NativeAskCard/);
  assert.match(app, /aria-label="Stop response" title="Stop this response without answering"/);
  assert.doesNotMatch(app, /JSON\.stringify\(approval\.params, null, 2\)/);
});

test("opening a chat jumps to its newest message without scrolling the window", () => {
  const app = readFileSync(new URL("../src/NativeApp.tsx", import.meta.url), "utf8");
  assert.doesNotMatch(app, /scrollIntoView/);
  assert.match(app, /pane\.scrollTop = pane\.scrollHeight/);
  assert.match(app, /setItems\(\[\]\); stickRef\.current = true;/);
});

test("team changes read as hiring, rules and changes, with the job text to check", () => {
  const hire = askView({ method: "mcpServer/elicitation/request", params: { serverName: "smara_team", message: "Allow the smara_team MCP server to run tool \"team_hire\"?",
    _meta: { tool_title: "Hire an employee", tool_params: { name: "Meera", role: "Mail handler", job: "You handle the inbox. Never send without asking." } } } });
  assert.equal(hire.title, "Hire Meera as Mail handler?");
  assert.match(hire.summary, /Never send without asking/);
  assert.equal(hire.kind, "team");
  const rule = askView({ method: "mcpServer/elicitation/request", params: { serverName: "smara_team", _meta: { tool_title: "Remember a rule", tool_params: { employee: "Kiran", rule: "Show totals in lakhs." } } } });
  assert.equal(rule.title, "Should Kiran remember this from now on?");
  assert.equal(rule.summary, "Show totals in lakhs.");
  const rename = askView({ method: "mcpServer/elicitation/request", params: { serverName: "smara_team", _meta: { tool_title: "Change an employee", tool_params: { employee: "smara", name: "Munshi" } } } });
  assert.equal(rename.title, "Change smara?");
  assert.match(rename.summary, /name → Munshi/);
});

test("a routine asks in plain words with the schedule", () => {
  const view = askView({ method: "mcpServer/elicitation/request", params: { message: "Allow the smara_team MCP server to run tool \"team_routine\"?", _meta: { tool_name: "team_routine", tool_params: { employee: "Kiran", task: "Check unpaid invoices", every: "day", at: "09:00" } } } });
  assert.equal(view.title, "Let Kiran do this every day at 09:00?");
  assert.equal(view.summary, "Check unpaid invoices");
});

test("removing an employee is a risky ask with the name", () => {
  const view = askView({ method: "mcpServer/elicitation/request", params: { message: "Allow the smara_team MCP server to run tool \"team_remove\"?", _meta: { tool_name: "team_remove", tool_params: { employee: "Zoya" } } } });
  assert.equal(view.title, "Remove Zoya from the team?");
  assert.equal(view.risky, true);
});
