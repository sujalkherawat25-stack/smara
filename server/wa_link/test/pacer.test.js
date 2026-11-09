import { test } from "node:test";
import assert from "node:assert/strict";
import { Pacer } from "../src/pacer.js";

test("jobs run one at a time, with the gap after the previous job ended", async () => {
  let clock = 0;
  const waits = [];
  const pacer = new Pacer({ gapMs: 5000, now: () => clock, sleep: async (ms) => { waits.push(ms); clock += ms; } });
  const order = [];
  const a = pacer.run(async () => { order.push("a"); clock += 100; return "a"; });
  const b = pacer.run(async () => { order.push("b"); return "b"; });
  assert.equal(await a, "a");
  assert.equal(await b, "b");
  assert.deepEqual(order, ["a", "b"]);
  assert.deepEqual(waits, [5000]);
});

test("a failing job does not block the next one", async () => {
  const pacer = new Pacer({ gapMs: 0, now: () => 0, sleep: async () => {} });
  await assert.rejects(pacer.run(async () => { throw new Error("boom"); }), /boom/);
  assert.equal(await pacer.run(async () => "next"), "next");
});
