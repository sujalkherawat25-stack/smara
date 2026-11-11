import { test } from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import { HttpError } from "../src/sessions.js";
import { makeHandler } from "../src/http.js";

const TOKEN = "s".repeat(40);
const KEY = "0b65d7d2-acc1-4842-8c1a-6460b692982a";
const AUTH = { authorization: `Bearer ${TOKEN}`, "content-type": "application/json" };

function fakeManager(overrides = {}) {
  return {
    count: () => 1,
    status: async () => ({ status: "connected", phone: "919812345678", qr_svg: "" }),
    start: async (key, auth) => ({ status: "connecting", phone: "", qr_svg: "", echoed: auth }),
    send: async () => "MSG-1",
    disconnect: async () => ({ status: "disconnected", phone: "" }),
    ...overrides,
  };
}

async function withServer(manager, fn, opts = {}) {
  const server = http.createServer(makeHandler({ manager, token: TOKEN, ...opts }));
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  const base = `http://127.0.0.1:${server.address().port}`;
  try {
    return await fn(base);
  } finally {
    await new Promise((r) => server.close(r));
  }
}

test("health is open; everything else needs the bearer token", async () => {
  await withServer(fakeManager(), async (base) => {
    assert.equal((await fetch(`${base}/healthz`)).status, 200);
    assert.equal((await fetch(`${base}/sessions/${KEY}`)).status, 401);
    assert.equal((await fetch(`${base}/sessions/${KEY}`, { headers: { authorization: "Bearer wrong" } })).status, 401);
    assert.equal((await fetch(`${base}/sessions/${KEY}`, { headers: AUTH })).status, 200);
  });
});

test("the media route hands over a held file's bytes only with the token, and only for a well-formed ref", async () => {
  const REF = "0b65d7d2-acc1-4842-8c1a-6460b692982b";
  const manager = fakeManager({
    media: async (key, ref) => {
      if (key !== KEY || ref !== REF) throw new HttpError(404, "no such file");
      return Buffer.from("PDF-BYTES");
    },
  });
  await withServer(manager, async (base) => {
    assert.equal((await fetch(`${base}/sessions/${KEY}/media/${REF}`)).status, 401);
    const ok = await fetch(`${base}/sessions/${KEY}/media/${REF}`, { headers: AUTH });
    assert.equal(ok.status, 200);
    assert.equal(ok.headers.get("content-type"), "application/octet-stream");
    assert.equal(await ok.text(), "PDF-BYTES");
    assert.equal((await fetch(`${base}/sessions/${KEY}/media/not-a-ref`, { headers: AUTH })).status, 404);
    assert.equal((await fetch(`${base}/sessions/${KEY}/media/00000000-0000-4000-8000-000000000000`, { headers: AUTH })).status, 404);
  });
});

test("unknown routes and malformed shop keys are 404", async () => {
  await withServer(fakeManager(), async (base) => {
    assert.equal((await fetch(`${base}/sessions/not-a-key`, { headers: AUTH })).status, 404);
    assert.equal((await fetch(`${base}/admin`, { headers: AUTH })).status, 404);
  });
});

test("send checks the recipient and the text before anything reaches WhatsApp", async () => {
  let sent = 0;
  await withServer(fakeManager({ send: async () => { sent++; return "M"; } }), async (base) => {
    const post = (body) => fetch(`${base}/sessions/${KEY}/send`, { method: "POST", headers: AUTH, body: JSON.stringify(body) });
    assert.equal((await post({ to: "+91 98765", text: "hi" })).status, 400);
    assert.equal((await post({ to: ["919812345678", "919812345679"], text: "hi" })).status, 400);
    assert.equal((await post({ to: "919812345678", text: "   " })).status, 400);
    assert.equal((await post({ to: "919812345678", text: "x".repeat(4001) })).status, 400);
    assert.equal(sent, 0);
    const ok = await post({ to: "919812345678", text: "Namaste" });
    assert.equal(ok.status, 200);
    assert.deepEqual(await ok.json(), { message_id: "M" });
    assert.equal(sent, 1);
  });
});

test("a failed send says only that it failed", async () => {
  const manager = fakeManager({ send: async () => { throw new Error("socket closed near 919812345678"); } });
  await withServer(manager, async (base) => {
    const res = await fetch(`${base}/sessions/${KEY}/send`, { method: "POST", headers: AUTH,
      body: JSON.stringify({ to: "919812345678", text: "hi" }) });
    assert.equal(res.status, 502);
    assert.equal(await res.text(), JSON.stringify({ error: "send failed" }));
  });
});

test("a send while not connected is a conflict", async () => {
  const manager = fakeManager({ send: async () => { throw new HttpError(409, "not connected"); } });
  await withServer(manager, async (base) => {
    const res = await fetch(`${base}/sessions/${KEY}/send`, { method: "POST", headers: AUTH,
      body: JSON.stringify({ to: "919812345678", text: "hi" }) });
    assert.equal(res.status, 409);
  });
});

test("bad JSON is refused, and an oversized body is refused", async () => {
  await withServer(fakeManager(), async (base) => {
    const bad = await fetch(`${base}/sessions/${KEY}/pair`, { method: "POST", headers: AUTH, body: "{nope" });
    assert.equal(bad.status, 400);
  });
  await withServer(fakeManager(), async (base) => {
    const big = await fetch(`${base}/sessions/${KEY}/pair`, { method: "POST", headers: AUTH,
      body: JSON.stringify({ auth: "x".repeat(5000) }) });
    assert.equal(big.status, 413);
  }, { maxBodyBytes: 1000 });
});

test("pair passes the stored auth through, and rejects a non-string", async () => {
  await withServer(fakeManager(), async (base) => {
    const ok = await fetch(`${base}/sessions/${KEY}/pair`, { method: "POST", headers: AUTH, body: JSON.stringify({ auth: "{}" }) });
    assert.equal((await ok.json()).echoed, "{}");
    const bad = await fetch(`${base}/sessions/${KEY}/pair`, { method: "POST", headers: AUTH, body: JSON.stringify({ auth: 5 }) });
    assert.equal(bad.status, 400);
  });
});

test("send-file checks the recipient and the size, and keeps the file name inside one folder", async () => {
  let sent = 0;
  let got = null;
  const manager = fakeManager({
    sendFile: async (key, to, file) => { sent++; got = { to, ...file }; return "F-1"; },
  });
  await withServer(manager, async (base) => {
    const post = (body) => fetch(`${base}/sessions/${KEY}/send-file`, { method: "POST", headers: AUTH, body: JSON.stringify(body) });
    const ok = await post({ to: "919000000001", filename: "../../etc/statement.pdf", mime: "application/pdf",
      caption: "Ravi", data_b64: Buffer.from("%PDF-1").toString("base64") });
    assert.equal(ok.status, 200);
    assert.deepEqual(await ok.json(), { message_id: "F-1" });
    assert.equal(got.filename.includes("/"), false);
    assert.equal(got.mime, "application/pdf");
    assert.equal(Buffer.from(got.data).toString(), "%PDF-1");
    assert.equal((await post({ to: "+91 98", data_b64: "eA==" })).status, 400);
    assert.equal((await post({ to: "919000000001", data_b64: "" })).status, 400);
    const tooBig = Buffer.alloc(20 * 1024 * 1024 + 1).toString("base64");
    assert.equal((await post({ to: "919000000001", data_b64: tooBig })).status, 400);
    assert.equal(sent, 1, "only the valid file reached the manager");
  });
});
