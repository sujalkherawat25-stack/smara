import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";
const module = { exports: {} };
const code = ts.transpileModule(readFileSync(new URL("../src/channelStatus.ts", import.meta.url), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
vm.runInNewContext(code, { module, exports: module.exports, URL });
const v = module.exports;
// vm runs the module in its own realm: copy results into this one so deepStrictEqual compares plain objects
const lines = (health) => JSON.parse(JSON.stringify(v.channelLines(health)));

const healthy = {
  channels: {
    telegram: { bot: true, owners_linked: 1 },
    whatsapp_link: { enabled: false },
    whatsapp_official: { configured: true },
  },
};

test("while loading, and when the server does not answer", () => {
  assert.deepEqual(lines(undefined), [{ label: "Channels: checking…", tone: "off" }]);
  assert.deepEqual(lines(null), [{ label: "Channels: server not reachable", tone: "warn" }]);
});

test("an older server without the channels block says so instead of guessing", () => {
  assert.deepEqual(lines({ ok: true }), [{ label: "Channels: not reported by this server", tone: "off" }]);
});

test("Telegram with an owner linked is ok; the switched-off WhatsApp link is off", () => {
  const rows = lines(healthy);
  assert.deepEqual(rows[0], { label: "Telegram: 1 owner linked", tone: "ok" });
  assert.deepEqual(rows[1], { label: "WhatsApp link: off", tone: "off" });
  assert.deepEqual(rows[2], { label: "WhatsApp (official number): configured", tone: "warn" });
});

test("plural owners, the bot with nobody linked, and Telegram not set up", () => {
  assert.equal(lines({ channels: { telegram: { bot: true, owners_linked: 3 } } })[0].label, "Telegram: 3 owners linked");
  assert.deepEqual(lines({ channels: { telegram: { bot: true, owners_linked: 0 } } })[0],
    { label: "Telegram: bot on, no owner linked", tone: "warn" });
  assert.deepEqual(lines({ channels: { telegram: { bot: false } } })[0], { label: "Telegram: not set up", tone: "off" });
});

test("an enabled WhatsApp link shows on; an unset official number says not set up", () => {
  const rows = lines({ channels: { whatsapp_link: { enabled: true }, whatsapp_official: { configured: false } } });
  assert.deepEqual(rows[1], { label: "WhatsApp link: on", tone: "ok" });
  assert.deepEqual(rows[2], { label: "WhatsApp (official number): not set up", tone: "off" });
});

test("the health page is read from the paired server's origin, with the default as fallback", () => {
  assert.equal(v.healthUrl("https://ai.syntarus.com/munim"), "https://ai.syntarus.com/smara/healthz");
  assert.equal(v.healthUrl("https://ai.syntarus.com/smara/"), "https://ai.syntarus.com/smara/healthz");
  assert.equal(v.healthUrl("http://127.0.0.1:8090/x"), "http://127.0.0.1:8090/smara/healthz");
  assert.equal(v.healthUrl(undefined), "https://ai.syntarus.com/smara/healthz");
  assert.equal(v.healthUrl("not a url"), "https://ai.syntarus.com/smara/healthz");
});
