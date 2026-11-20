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

test("saving a skill shows the workflow as numbered steps to check", () => {
  const view = askView({ method: "mcpServer/elicitation/request", params: { message: "Allow the smara_team MCP server to run tool \"skill_save\"?", _meta: { tool_name: "skill_save",
    tool_params: { employee: "kiran", name: "Monthly sales summary", when_to_use: "When asked for a month's sales", steps: ["Read sales_<M>.csv", "Skip cancelled rows"], output: "summary_<M>.md", never: ["Edit the csv"] } } } });
  assert.equal(view.title, "Teach Kiran “Monthly sales summary”?");
  assert.equal(view.summary, "Use when: When asked for a month's sales\n\n1. Read sales_<M>.csv\n2. Skip cancelled rows");
  assert.match(view.detail, /Output: summary_<M>\.md/);
  assert.match(view.detail, /Never: Edit the csv/);
  const forget = askView({ method: "mcpServer/elicitation/request", params: { message: "x", _meta: { tool_name: "skill_forget", tool_params: { employee: "kiran", name: "Monthly sales summary" } } } });
  assert.equal(forget.risky, true);
});

test("asking for app access in chat names the apps, the time and the safety limits", () => {
  const view = askView({ method: "mcpServer/elicitation/request", params: { serverName: "smara_computer", message: "Allow the smara_computer MCP server to run tool \"computer_request_access\"?",
    _meta: { tool_name: "computer_request_access", tool_params: { apps: ["chrome", "tally"], minutes: 30, reason: "Open your LinkedIn to check notifications" } } } });
  assert.equal(view.title, "Let Smara use chrome, tally for 30 minutes?");
  assert.equal(view.summary, "Open your LinkedIn to check notifications");
  assert.match(view.detail, /Pause or Stop/);
  assert.match(view.detail, /never types passwords/i);
  assert.equal(view.kind, "computer");
});

test("a watch asks in plain words and says it stays quiet", () => {
  const card = askView({ method: "mcpServer/elicitation/request", params: { serverName: "smara_team", message: "Set up a routine", _meta: { tool_name: "team_routine", tool_params: { employee: "Kiran", task: "Check the Gmail inbox", every: "15min", watch_for: "a mail from the GST portal" } } } });
  assert.match(card.title, /watch for a mail from the GST portal/);
  assert.match(card.summary, /Checks every 15 minutes/);
  assert.match(card.detail, /Stays quiet/);
});
