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

test("a LID sender with no phone-number address is mapped through Baileys, and an unknown one is skipped", async () => {
  const h = harness();
  await h.manager.start(KEY);
  const sock = h.sockets[0];
  sock.signalRepository = { lidMapping: { getPNForLID: async (lid) => (lid === "555000111@lid" ? "919000000003:0@s.whatsapp.net" : null) } };
  sock.emit("messages.upsert", { type: "notify", messages: [
    { key: { id: "M1", remoteJid: "555000111@lid", fromMe: false }, message: { conversation: "paid" } },
    { key: { id: "M2", remoteJid: "999000999@lid", fromMe: false }, message: { conversation: "unknown" } },
  ] });
  await h.settle();
  const inbound = h.calls.filter((c) => c.path === "inbound").map((c) => c.body);
  assert.deepEqual(inbound, [{ tenant_id: KEY, wamid: "wl:M1", sender: "919000000003", kind: "text", text: "paid", ts: 0 }]);
});

test("content extraction keeps text and captions, and describes media without downloading it", () => {
  assert.deepEqual(extractContent({ conversation: "hi" }), { kind: "text", text: "hi" });
  assert.deepEqual(extractContent({ extendedTextMessage: { text: "yo" } }), { kind: "text", text: "yo" });
  assert.deepEqual(extractContent({ imageMessage: { caption: "bill", mimetype: "image/jpeg", fileLength: 1000 } }),
    { kind: "image", text: "bill", media: { mime: "image/jpeg", filename: "", size: 1000 } });
  assert.deepEqual(extractContent({ audioMessage: {} }), { kind: "audio", text: "", media: { mime: "", filename: "", size: 0 } });
  assert.deepEqual(extractContent({ documentMessage: { fileName: "ledger.xlsx", mimetype: "application/vnd.ms-excel",
    fileLength: { toNumber: () => 2048 } } }).media, { mime: "application/vnd.ms-excel", filename: "ledger.xlsx", size: 2048 });
  assert.deepEqual(extractContent({ ephemeralMessage: { message: { conversation: "wrapped" } } }), { kind: "text", text: "wrapped" });
  assert.deepEqual(extractContent({ stickerMessage: {} }), { kind: "sticker", text: "" });
  assert.deepEqual(extractContent({ reactionMessage: {} }), { kind: "other", text: "" });
});

test("a voice note is held in the sidecar: the app gets a ref, and the bytes come only when it asks, once", async () => {
  const fetched = [];
  const h = harness({ downloadMedia: async (message) => { fetched.push(message.key.id); return Buffer.from("OGG-BYTES"); } });
  await h.manager.start(KEY);
  h.sockets[0].emit("messages.upsert", { type: "notify", messages: [
    { key: { id: "V1", remoteJid: "919000000001@s.whatsapp.net", fromMe: false },
      message: { audioMessage: { mimetype: "audio/ogg; codecs=opus", fileLength: 4000 } }, messageTimestamp: 1 }] });
  await h.settle();
  const body = h.last("inbound");
  assert.equal(body.kind, "audio");
  assert.match(body.media.ref, /^[0-9a-f-]{36}$/i);
  assert.equal(body.media.size, 4000);
  assert.deepEqual(fetched, [], "nothing is downloaded until the app asks");
  assert.equal((await h.manager.media(KEY, body.media.ref)).toString(), "OGG-BYTES");
  assert.deepEqual(fetched, ["V1"]);
  await assert.rejects(h.manager.media(KEY, body.media.ref), (e) => e.status === 404, "a file is handed over once");
});

test("a file over 20 MB is not held: the app is told its size and gets no ref", async () => {
  const h = harness({ downloadMedia: async () => { throw new Error("must not download"); } });
  await h.manager.start(KEY);
  h.sockets[0].emit("messages.upsert", { type: "notify", messages: [
    { key: { id: "D1", remoteJid: "919000000001@s.whatsapp.net", fromMe: false },
      message: { documentMessage: { fileName: "huge.pdf", mimetype: "application/pdf", fileLength: 25 * 1024 * 1024 } } }] });
  await h.settle();
  const body = h.last("inbound");
  assert.equal(body.media.ref, null);
  assert.equal(body.media.size, 25 * 1024 * 1024);
  await assert.rejects(h.manager.media(KEY, "00000000-0000-4000-8000-000000000000"), (e) => e.status === 404);
});

test("a held file past its time is forgotten, and a download larger than the limit is refused", async () => {
  const h = harness({ downloadMedia: async () => Buffer.alloc(21 * 1024 * 1024) });
  await h.manager.start(KEY);
  h.sockets[0].emit("messages.upsert", { type: "notify", messages: [
    { key: { id: "E1", remoteJid: "919000000001@s.whatsapp.net", fromMe: false },
      message: { videoMessage: { mimetype: "video/mp4", fileLength: 100 } } }] });
  await h.settle();
  const ref = h.last("inbound").media.ref;
  h.manager.sessions.get(KEY).media.get(ref).expires = 0;
  await assert.rejects(h.manager.media(KEY, ref), (e) => e.status === 404, "expired");
  h.sockets[0].emit("messages.upsert", { type: "notify", messages: [
    { key: { id: "E2", remoteJid: "919000000001@s.whatsapp.net", fromMe: false },
      message: { videoMessage: { mimetype: "video/mp4", fileLength: 100 } } }] });
  await h.settle();
  const big = h.last("inbound").media.ref;
  await assert.rejects(h.manager.media(KEY, big), (e) => e.status === 413, "a download over the limit is refused");
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

test("a file goes as a document, and an image goes as an image, to one number", async () => {
  const h = harness();
  await h.manager.start(KEY);
  h.sockets[0].emit("connection.update", { connection: "open" });
  const pdf = Buffer.from("%PDF-1.4 test");
  await h.manager.sendFile(KEY, "919000000001", { data: pdf, mime: "application/pdf", filename: "statement.pdf", caption: "Ravi" });
  await h.manager.sendFile(KEY, "919000000001", { data: Buffer.from([1, 2, 3]), mime: "image/jpeg", filename: "bill.jpg", caption: "" });
  const [doc, img] = h.sockets[0].sent;
  assert.equal(doc.jid, "919000000001@s.whatsapp.net");
  assert.deepEqual(doc.content, { document: pdf, mimetype: "application/pdf", fileName: "statement.pdf", caption: "Ravi" });
  assert.equal(img.content.image.length, 3);
  assert.equal(img.content.document, undefined);
});

test("no file is sent unless the session is connected", async () => {
  const h = harness();
  await h.manager.start(KEY);
  await assert.rejects(h.manager.sendFile(KEY, "919000000001", { data: Buffer.from("x"), mime: "application/pdf", filename: "a.pdf" }),
    (e) => e instanceof HttpError && e.status === 409);
});
