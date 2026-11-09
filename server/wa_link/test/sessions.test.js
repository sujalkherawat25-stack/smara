import { test } from "node:test";
import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { DisconnectReason } from "@whiskeysockets/baileys";
import { HttpError } from "../src/sessions.js";
import { SessionManager, extractContent, phoneOf } from "../src/sessions.js";
import { parseState } from "../src/state.js";

const KEY = "0b65d7d2-acc1-4842-8c1a-6460b692982a";
const SHOP_PHONE = "919812345678";

// A stand-in for a Baileys socket: an event emitter with the few methods the sidecar uses.
function fakeSockets() {
  const sockets = [];
  const makeSocket = (cfg) => {
    const sock = new EventEmitter();
    sock.ev = sock;
    sock.cfg = cfg;
    sock.user = { id: `${SHOP_PHONE}:12@s.whatsapp.net` };
    sock.sent = [];
    sock.sendMessage = async (jid, content) => {
      sock.sent.push({ jid, content });
      return { key: { id: `SENT-${sock.sent.length}` } };
    };
    sock.end = () => {};
    sockets.push(sock);
    return sock;
  };
  return { makeSocket, sockets };
}

function harness(overrides = {}) {
  const calls = [];
  const callback = async (path, body) => {
    calls.push({ path, body });
    return true;
  };
  const { makeSocket, sockets } = fakeSockets();
  const manager = new SessionManager({
    callback, logger: {}, makeSocket, sendGapMs: 0, flushDelayMs: 0, reconnectBaseMs: 1, ...overrides,
  });
  const settle = () => new Promise((r) => setTimeout(r, 20));
  return { manager, calls, sockets, settle, last: (path) => calls.filter((c) => c.path === path).at(-1)?.body };
}

test("a fresh pairing shows a QR, and the QR is never sent to the app", async () => {
  const h = harness();
  const started = await h.manager.start(KEY);
  assert.equal(started.status, "connecting");
  const sock = h.sockets[0];
  sock.emit("connection.update", { qr: "2@FAKE-QR-PAYLOAD" });
  const status = await h.manager.status(KEY);
  assert.equal(status.status, "pairing");
  assert.ok(status.qr_svg.startsWith("<svg"), "owner page gets an SVG");
  await h.settle();
  assert.deepEqual(h.last("status"), { tenant_id: KEY, status: "pairing", phone: "" });
  assert.ok(!JSON.stringify(h.calls).includes("FAKE-QR-PAYLOAD"), "the QR string must not leave the sidecar");
});

test("a pairing nobody scans is abandoned, not retried forever", async () => {
  const h = harness({ pairingTimeoutMs: 20 });
  await h.manager.start(KEY);
  await new Promise((r) => setTimeout(r, 60));
  assert.equal(h.manager.count(), 0);
  assert.equal(h.last("status").status, "disconnected");
});

test("linking the device reports connected with its number and saves the auth state", async () => {
  const h = harness();
  await h.manager.start(KEY);
  const sock = h.sockets[0];
  sock.emit("creds.update", { registrationId: 4242 });
  sock.emit("connection.update", { connection: "open" });
  await h.settle();
  assert.deepEqual(h.last("status"), { tenant_id: KEY, status: "connected", phone: SHOP_PHONE });
  const state = parseState(h.last("state").auth);
  assert.equal(state.creds.registrationId, 4242);
  assert.equal((await h.manager.status(KEY)).status, "connected");
});

test("a send goes to one number, as text, and returns WhatsApp's id", async () => {
  const h = harness();
  await h.manager.start(KEY);
  h.sockets[0].emit("connection.update", { connection: "open" });
  const id = await h.manager.send(KEY, "919000000001", "Namaste Ravi ji");
  assert.equal(id, "SENT-1");
  assert.deepEqual(h.sockets[0].sent, [{ jid: "919000000001@s.whatsapp.net", content: { text: "Namaste Ravi ji" } }]);
});

test("nothing is sent unless the session is connected", async () => {
  const h = harness();
  await h.manager.start(KEY);
  await assert.rejects(h.manager.send(KEY, "919000000001", "hi"), (e) => e instanceof HttpError && e.status === 409);
  await assert.rejects(h.manager.send("00000000-0000-4000-8000-000000000000", "919000000001", "hi"),
    (e) => e instanceof HttpError && e.status === 409);
  assert.equal(h.sockets[0].sent.length, 0);
});

test("WhatsApp logging the device out pauses it, forgets its state and tells the app", async () => {
  const h = harness();
  await h.manager.start(KEY);
  h.sockets[0].emit("connection.update", { connection: "open" });
  await h.settle();
  const before = h.calls.length;
  h.sockets[0].emit("connection.update", {
    connection: "close",
    lastDisconnect: { error: { output: { statusCode: DisconnectReason.loggedOut } } },
  });
  await h.settle();
  assert.equal(h.last("status").status, "logged_out");
  assert.equal(h.manager.count(), 0);
  assert.equal((await h.manager.status(KEY)).status, "absent");
  // no further state is written after the logout: the app wipes what it holds
  const after = h.calls.slice(before).filter((c) => c.path === "state");
  assert.equal(after.length, 0);
  await assert.rejects(h.manager.send(KEY, "919000000001", "hi"));
});

test("a dropped connection after linking reconnects with the same credentials", async () => {
  const h = harness();
  await h.manager.start(KEY);
  h.sockets[0].emit("connection.update", { connection: "open" });
  h.sockets[0].emit("connection.update", { connection: "close", lastDisconnect: { error: { output: { statusCode: 428 } } } });
  await h.settle();
  assert.equal(h.last("status").status, "reconnecting");
  assert.equal(h.sockets.length, 2, "a new socket was opened");
  assert.equal(h.sockets[1].cfg.auth.creds, h.sockets[0].cfg.auth.creds, "same credentials");
  h.sockets[1].emit("connection.update", { connection: "open" });
  await h.settle();
  assert.equal(h.last("status").status, "connected");
});

test("a deliberate disconnect stops the device and does not reconnect", async () => {
  const h = harness();
  await h.manager.start(KEY);
  h.sockets[0].emit("connection.update", { connection: "open" });
  const res = await h.manager.disconnect(KEY);
  assert.equal(res.status, "disconnected");
  h.sockets[0].emit("connection.update", { connection: "close", lastDisconnect: { error: { output: { statusCode: 428 } } } });
  await new Promise((r) => setTimeout(r, 30));
  assert.equal(h.sockets.length, 1, "no reconnect after a deliberate disconnect");
  assert.equal(h.last("status").status, "disconnected");
});

test("messages from customers arrive as inbound events; our own and group messages do not", async () => {
  const h = harness();
  await h.manager.start(KEY);
  const sock = h.sockets[0];
  sock.emit("messages.upsert", {
    type: "notify",
    messages: [
      { key: { id: "A1", remoteJid: "919000000001@s.whatsapp.net", fromMe: false },
        message: { conversation: "STOP" }, messageTimestamp: 1700000000 },
      { key: { id: "A2", remoteJid: "919000000001@s.whatsapp.net", fromMe: true }, message: { conversation: "mine" } },
      { key: { id: "A3", remoteJid: "120363@g.us", fromMe: false }, message: { conversation: "group" } },
    ],
  });
  sock.emit("messages.upsert", { type: "append", messages: [
    { key: { id: "A4", remoteJid: "919000000001@s.whatsapp.net", fromMe: false }, message: { conversation: "old" } }] });
  await h.settle();
  const inbound = h.calls.filter((c) => c.path === "inbound").map((c) => c.body);
  assert.deepEqual(inbound, [{ tenant_id: KEY, wamid: "wl:A1", sender: "919000000001", kind: "text", text: "STOP", ts: 1700000000 }]);
});

test("a message sent from a LID address is matched by its phone-number address", async () => {
  const h = harness();
  await h.manager.start(KEY);
  h.sockets[0].emit("messages.upsert", { type: "notify", messages: [
    { key: { id: "L1", remoteJid: "123456789@lid", remoteJidAlt: "919000000002@s.whatsapp.net", fromMe: false },
      message: { extendedTextMessage: { text: "paid 500" } } }] });
  await h.settle();
  assert.equal(h.last("inbound").sender, "919000000002");
  assert.equal(h.last("inbound").text, "paid 500");
});

test("content extraction keeps text and captions, and labels media without downloading it", () => {
  assert.deepEqual(extractContent({ conversation: "hi" }), { kind: "text", text: "hi" });
  assert.deepEqual(extractContent({ extendedTextMessage: { text: "yo" } }), { kind: "text", text: "yo" });
  assert.deepEqual(extractContent({ imageMessage: { caption: "bill" } }), { kind: "image", text: "bill" });
  assert.deepEqual(extractContent({ audioMessage: {} }), { kind: "audio", text: "" });
  assert.deepEqual(extractContent({ ephemeralMessage: { message: { conversation: "wrapped" } } }), { kind: "text", text: "wrapped" });
  assert.deepEqual(extractContent({ reactionMessage: {} }), { kind: "other", text: "" });
});

test("phone numbers are read from WhatsApp ids", () => {
  assert.equal(phoneOf("919812345678:12@s.whatsapp.net"), "919812345678");
  assert.equal(phoneOf(""), "");
});

test("the auth state is held in memory: key updates are flushed to the app, not to disk", async () => {
  const h = harness();
  await h.manager.start(KEY);
  const session = h.manager.sessions.get(KEY);
  await session.keyStore.set({ "pre-key": { 9: { public: Buffer.from("pub"), private: Buffer.from("prv") } } });
  await h.settle();
  const state = parseState(h.last("state").auth);
  assert.ok(state.keys["pre-key"][9]);
});
