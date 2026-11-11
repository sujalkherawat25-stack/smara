import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";
const module = { exports: {} };
const code = ts.transpileModule(readFileSync(new URL("../src/shopOverview.ts", import.meta.url), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
vm.runInNewContext(code, { module, exports: module.exports });
const v = module.exports;
const NOW = 1_800_000_000;
// vm runs the module in its own realm: copy results into this one so deepStrictEqual compares plain objects
const lines = (overview, now = NOW) => JSON.parse(JSON.stringify(v.overviewLines(overview, now)));

const shop = {
  shop: "Demo", fetched_at: NOW - 30,
  dues: { open: 12, open_paise: 123456700, overdue: 3, overdue_paise: 4500000 },
  payables: { open: 2, open_paise: 999900 },
  waiting_for_owner: 2,
  channels: { telegram_linked: true, whatsapp_window_open: false, last_used: "telegram" },
  tally_last_sync_at: new Date((NOW - 3 * 3600) * 1000).toISOString(),
};

test("rupees are grouped the Indian way", () => {
  assert.equal(v.rupees(123456700), "₹12,34,567");
  assert.equal(v.rupees(99900), "₹999");
  assert.equal(v.rupees(10000000), "₹1,00,000");
  assert.equal(v.rupees(0), "₹0");
});

test("nothing is shown until the helper has fetched the shop's numbers", () => {
  assert.deepEqual(lines(null), []);
  assert.deepEqual(lines(undefined), []);
  assert.deepEqual(lines({ shop: "Demo" }), []);
});

test("a shop with dues, approvals waiting, Telegram linked and a recent Tally sync", () => {
  assert.deepEqual(lines(shop), [
    { label: "To collect: ₹12,34,567 (12 open)", tone: "off" },
    { label: "Overdue: ₹45,000 (3)", tone: "warn" },
    { label: "To pay: ₹9,999 (2 bills)", tone: "off" },
    { label: "2 waiting for your approval in Telegram", tone: "warn" },
    { label: "Your Telegram: linked", tone: "ok" },
    { label: "Tally synced 3 h ago", tone: "ok" },
  ]);
});

test("a clean shop: nothing overdue, nothing waiting, no Tally, Telegram not linked", () => {
  assert.deepEqual(lines({ fetched_at: NOW, dues: { open: 0 }, channels: { last_used: "whatsapp" } }), [
    { label: "To collect: ₹0 (0 open)", tone: "off" },
    { label: "Overdue: none", tone: "ok" },
    { label: "Nothing waiting for your approval", tone: "ok" },
    { label: "Your Telegram: not linked", tone: "warn" },
    { label: "Tally: not synced yet", tone: "off" },
  ]);
});

test("old numbers and an old Tally sync are flagged", () => {
  const old = { ...shop, fetched_at: NOW - 2 * 3600, tally_last_sync_at: new Date((NOW - 5 * 86400) * 1000).toISOString() };
  const rows = lines(old);
  assert.deepEqual(rows.at(-2), { label: "Tally synced 5 days ago", tone: "warn" });
  assert.deepEqual(rows.at(-1), { label: "Numbers last updated 2 h ago", tone: "warn" });
  assert.ok(!lines(shop).some((l) => l.label.startsWith("Numbers last updated")));
});
