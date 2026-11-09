import { test } from "node:test";
import assert from "node:assert/strict";
import { makeCallback } from "../src/callbacks.js";

const TOKEN = "t".repeat(40);

function stub(statuses) {
  const calls = [];
  const fetchImpl = async (url, init) => {
    calls.push({ url, init });
    const status = statuses[Math.min(calls.length - 1, statuses.length - 1)];
    return { ok: status < 400, status };
  };
  return { calls, fetchImpl };
}

test("transient failures are retried, and the bearer token is sent", async () => {
  const { calls, fetchImpl } = stub([500, 200]);
  const post = makeCallback({ baseUrl: "http://app:8080/internal/wa-link/", token: TOKEN, fetchImpl, sleep: async () => {} });
  assert.equal(await post("state", { tenant_id: "x", auth: "{}" }), true);
  assert.equal(calls.length, 2);
  assert.equal(calls[0].url, "http://app:8080/internal/wa-link/state");
  assert.equal(calls[0].init.headers.authorization, `Bearer ${TOKEN}`);
});

test("a refusal (4xx) is not retried", async () => {
  const { calls, fetchImpl } = stub([401]);
  const post = makeCallback({ baseUrl: "http://app:8080/internal/wa-link", token: TOKEN, fetchImpl, sleep: async () => {} });
  assert.equal(await post("status", {}), false);
  assert.equal(calls.length, 1);
});

test("failure logs never carry the token or the message body", async () => {
  const lines = [];
  const fetchImpl = async () => { throw new TypeError("connect ECONNREFUSED"); };
  const post = makeCallback({ baseUrl: "http://app", token: TOKEN, fetchImpl, retries: 2, sleep: async () => {},
                              log: (level, msg) => lines.push(msg) });
  assert.equal(await post("inbound", { text: "my private message" }), false);
  assert.equal(lines.length, 1);
  assert.ok(!lines.join(" ").includes(TOKEN));
  assert.ok(!lines.join(" ").includes("private message"));
});
