import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";
const module = { exports: {} };
const code = ts.transpileModule(readFileSync(new URL("../src/controlBarView.ts", import.meta.url), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
vm.runInNewContext(code, { module, exports: module.exports });
const v = module.exports;
const state = over => ({ active: true, paused: false, until: 1763460000, app: "excel.exe", title: "Book1", action: "type_text",
  last_ts: 1000, hotkey: "Ctrl+Alt+Esc", hotkey_ok: true, ...over });

test("the bar says plainly who is in control and what it just did", () => {
  assert.equal(v.headline(state()), "Smara is controlling your PC");
  assert.equal(v.headline(state({ paused: true })), "Smara is paused");
  assert.equal(v.headline(state({ agent: "Kiran: Tally day book" })), "Kiran (Smara) is controlling your PC");
  assert.equal(v.headline(state({ agent: "Smara chat" })), "Smara is controlling your PC");
  assert.equal(v.lastStep(state(), 1060), "typing in Excel");
  assert.equal(v.lastStep(state({ app: "tallyprime.exe", action: "press_key" }), 1001), "pressing a key in TallyPrime");
  assert.equal(v.lastStep(state(), 1000 + 121), "");                         // old news is not shown
  assert.match(v.untilLabel(state()), /^until \d\d:\d\d$/);
  assert.equal(v.isControlBar("#control-bar"), true);
  assert.equal(v.isControlBar(""), false);
});

test("the bar window has Pause/Resume and Stop wired to the Rust commands", () => {
  const bar = readFileSync(new URL("../src/ControlBar.tsx", import.meta.url), "utf8");
  assert.match(bar, /computer_bar_pause", \{ paused: !state\.paused \}/);
  assert.match(bar, /computer_bar_stop/);
  const main = readFileSync(new URL("../src/main.tsx", import.meta.url), "utf8");
  assert.match(main, /bar \? <ControlBar \/> : <App \/>/);
  const caps = JSON.parse(readFileSync(new URL("../src-tauri/capabilities/default.json", import.meta.url), "utf8"));
  assert.ok(caps.windows.includes("control-bar"));
});

test("Settings grants only the ticked apps unless the owner picks any app, and both ask first", () => {
  const page = readFileSync(new URL("../src/NativeAutomation.tsx", import.meta.url), "utf8");
  assert.match(page, /"grant_desktop", \{ minutes: 30 \}\)/);
  assert.match(page, /"grant_desktop", \{ minutes: 30, any_app: true \}\)/);
  assert.equal((page.match(/window\.confirm\("Let Smara control/g) || []).length, 2);
  assert.match(page, /"save_apps", \{ apps \}/);
});
