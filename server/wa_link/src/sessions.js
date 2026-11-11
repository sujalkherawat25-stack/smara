// One Baileys socket per shop (tenant). Pairing shows a QR; once linked, the device keeps running until WhatsApp
// logs it out. Every state change is reported to the Smara app, which owns the database and the encryption.
import { randomUUID } from "node:crypto";
import makeWASocket, { Browsers, DisconnectReason, downloadMediaMessage, makeCacheableSignalKeyStore } from "@whiskeysockets/baileys";
import QRCode from "qrcode";
import { Pacer } from "./pacer.js";
import { freshState, makeKeyStore, parseState, serializeState } from "./state.js";

export class HttpError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

const FLUSH_DELAY_MS = 1500;
const RECONNECT_BASE_MS = 2000;
const RECONNECT_MAX_MS = 60_000;
const PAIRING_TIMEOUT_MS = 10 * 60_000;   // an unscanned QR is abandoned after ten minutes, not retried forever
export const MAX_MEDIA_BYTES = 20 * 1024 * 1024;   // the same cap the app applies to every file the owner sends
const MEDIA_TTL_MS = 15 * 60_000;         // a received file is held this long for the app to fetch it, then forgotten
const MAX_HELD_MEDIA = 50;                // per shop; the oldest is dropped first

// The digits of a WhatsApp id such as "919812345678:12@s.whatsapp.net".
export function phoneOf(jid = "") {
  return String(jid).split(":")[0].split("@")[0].replace(/\D/g, "");
}

function unwrap(message) {
  for (const key of ["ephemeralMessage", "viewOnceMessage", "viewOnceMessageV2", "documentWithCaptionMessage"]) {
    if (message[key]?.message) return message[key].message;
  }
  return message;
}

function toNumber(v) {
  if (typeof v === "number") return v;
  if (v && typeof v.toNumber === "function") return v.toNumber();   // a protobuf Long
  return Number(v) || 0;
}

// What was sent, as the app needs it: text (or a caption) and a coarse kind. A file or voice note also carries its
// type, name and size (`media`); the bytes are not downloaded here, only when the app asks for them.
export function extractContent(raw = {}) {
  const m = unwrap(raw);
  if (m.conversation) return { kind: "text", text: m.conversation };
  if (m.extendedTextMessage) return { kind: "text", text: m.extendedTextMessage.text || "" };
  if (m.imageMessage) return mediaContent("image", m.imageMessage, m.imageMessage.caption || "");
  if (m.videoMessage) return mediaContent("video", m.videoMessage, m.videoMessage.caption || "");
  if (m.documentMessage) return mediaContent("document", m.documentMessage, m.documentMessage.caption || "", m.documentMessage.fileName);
  if (m.audioMessage) return mediaContent("audio", m.audioMessage, "");
  if (m.stickerMessage) return { kind: "sticker", text: "" };
  return { kind: "other", text: "" };
}

function mediaContent(kind, part, text, fileName = "") {
  return {
    kind,
    text,
    media: { mime: String(part.mimetype || "").slice(0, 120), filename: String(fileName || "").slice(0, 255), size: toNumber(part.fileLength) },
  };
}

class Session {
  constructor(key, state, { sendGapMs, onKeysChanged }) {
    this.key = key;
    this.creds = state.creds;
    this.keyStore = makeKeyStore(state.keys, onKeysChanged);
    this.status = "connecting";     // connecting | pairing | connected | reconnecting | disconnected | logged_out
    this.phone = "";
    this.qr = null;                 // the QR string while pairing; never stored or logged
    this.sock = null;
    this.stopped = false;           // closed on purpose: never reconnect
    this.loggedOut = false;         // WhatsApp unlinked the device: its state must be forgotten, never flushed again
    this.wasConnected = false;
    this.reportedStatus = null;
    this.attempts = 0;
    this.flushTimer = null;
    this.chain = Promise.resolve(); // every call to the app runs here, in order, one at a time
    this.pacer = new Pacer({ gapMs: sendGapMs });
    this.media = new Map();         // ref -> { message, expires }: files received, held until fetched or expired
  }
}

export class SessionManager {
  constructor({
    callback,
    logger,
    makeSocket = (cfg) => makeWASocket(cfg),
    downloadMedia = null,
    sendGapMs = 3000,
    flushDelayMs = FLUSH_DELAY_MS,
    reconnectBaseMs = RECONNECT_BASE_MS,
    pairingTimeoutMs = PAIRING_TIMEOUT_MS,
    log = () => {},
  }) {
    Object.assign(this, { callback, logger, makeSocket, sendGapMs, flushDelayMs, reconnectBaseMs, pairingTimeoutMs, log });
    // Fetches the bytes of one held message. Baileys decrypts it; the socket can refresh an expired media URL.
    this.downloadMedia = downloadMedia ?? ((message, sock) => downloadMediaMessage(message, "buffer", {}, {
      logger,
      reuploadRequest: (msg) => sock.updateMediaMessage(msg),
    }));
    this.sessions = new Map();
  }

  count() {
    return this.sessions.size;
  }

  async status(key) {
    const s = this.sessions.get(key);
    if (!s) return { status: "absent", phone: "", qr_svg: "" };
    const qr_svg = s.qr ? await QRCode.toString(s.qr, { type: "svg", margin: 1, errorCorrectionLevel: "M" }) : "";
    return { status: s.status, phone: s.phone, qr_svg };
  }

  // Start (or resume) a shop's link. With no auth it starts a fresh pairing, which yields a QR; with the stored auth
  // state it reconnects the device the owner already linked.
  async start(key, authText = null) {
    if (this.sessions.has(key)) return this.status(key);
    const state = authText ? parseState(authText) : freshState();
    const s = new Session(key, state, { sendGapMs: this.sendGapMs, onKeysChanged: () => this.scheduleFlush(s) });
    this.sessions.set(key, s);
    if (!authText) {
      s.pairTimer = setTimeout(() => {
        if (!s.wasConnected && this.sessions.get(key) === s) {
          this.log("info", "pairing timed out without a scan");
          this.disconnect(key);
        }
      }, this.pairingTimeoutMs);
      s.pairTimer.unref?.();
    }
    this.connect(s);
    return this.status(key);
  }

  connect(s) {
    s.stopped = false;
    const sock = this.makeSocket({
      auth: { creds: s.creds, keys: makeCacheableSignalKeyStore(s.keyStore, this.logger) },
      logger: this.logger,
      browser: Browsers.macOS("Desktop"),
      printQRInTerminal: false,
      markOnlineOnConnect: false,
      syncFullHistory: false,
      generateHighQualityLinkPreview: false,
    });
    s.sock = sock;
    sock.ev.on("creds.update", (update) => {
      Object.assign(s.creds, update);
      this.scheduleFlush(s);
    });
    sock.ev.on("connection.update", (update) => this.onConnection(s, sock, update));
    sock.ev.on("messages.upsert", (update) => this.onMessages(s, update).catch((err) => this.log("warn", `inbound handling failed: ${err?.name || "error"}`)));
  }

  onConnection(s, sock, { connection, lastDisconnect, qr }) {
    if (sock !== s.sock) return; // an old socket that closed late
    if (qr) {
      s.qr = qr;
      s.status = "pairing";
      this.report(s, "pairing");
    }
    if (connection === "open") {
      clearTimeout(s.pairTimer);
      s.qr = null;
      s.attempts = 0;
      s.wasConnected = true;
      s.phone = phoneOf(sock.user?.id);
      s.status = "connected";
      this.report(s, "connected");
      this.flushNow(s);
      return;
    }
    if (connection !== "close") return;
    s.sock = null;
    s.qr = null;
    const code = lastDisconnect?.error?.output?.statusCode;
    if (code === DisconnectReason.loggedOut) {
      s.loggedOut = true;
      s.stopped = true;
      s.status = "logged_out";
      clearTimeout(s.flushTimer);
      this.report(s, "logged_out");
      this.sessions.delete(s.key);
      return;
    }
    if (s.stopped) return;
    this.flushNow(s);
    s.status = s.wasConnected ? "reconnecting" : "connecting";
    if (s.wasConnected) this.report(s, "reconnecting");
    s.attempts += 1;
    const delay = Math.min(RECONNECT_MAX_MS, this.reconnectBaseMs * 2 ** Math.min(s.attempts - 1, 6));
    setTimeout(() => {
      if (!s.stopped && this.sessions.get(s.key) === s) this.connect(s);
    }, delay).unref?.();
  }

  async onMessages(s, { messages = [], type }) {
    // Diagnostics: counts and address shapes only, never message text or full numbers.
    this.log("info", `messages event type=${type} count=${messages.length}`);
    if (type !== "notify") return; // history sync and other bulk types are not customer messages
    for (const m of messages) {
      let jid = [m.key?.remoteJid, m.key?.remoteJidAlt].find((j) => typeof j === "string" && j.endsWith("@s.whatsapp.net"));
      // Newer WhatsApp addresses many senders by an anonymous @lid id. Baileys maps it back to the phone number.
      if (!jid && !m.key?.fromMe && typeof m.key?.remoteJid === "string" && m.key.remoteJid.endsWith("@lid")) {
        try {
          const pn = await s.sock?.signalRepository?.lidMapping?.getPNForLID(m.key.remoteJid);
          if (typeof pn === "string" && pn.endsWith("@s.whatsapp.net")) jid = pn;
        } catch {
          // no mapping yet: the message is skipped below and counted
        }
      }
      if (m.key?.fromMe || !m.key?.id || !jid) {
        const suffix = (j) => (typeof j === "string" ? j.split("@")[1] || "none" : "none");
        this.log("info", `message skipped fromMe=${!!m.key?.fromMe} hasId=${!!m.key?.id} remote=${suffix(m.key?.remoteJid)} alt=${suffix(m.key?.remoteJidAlt)}`);
        continue;
      }
      const { kind, text, media } = extractContent(m.message || {});
      const body = {
        tenant_id: s.key,
        wamid: `wl:${m.key.id}`,
        sender: phoneOf(jid),
        kind,
        text: text.slice(0, 4000),
        ts: Number(m.messageTimestamp) || 0,
      };
      if (media) body.media = this.holdMedia(s, m, media);
      this.enqueue(s, "inbound", body);
    }
  }

  // Keeps the received message so the app can fetch its file. Too large to hold: no ref, and the app refuses it by size.
  holdMedia(s, message, media) {
    if (media.size > MAX_MEDIA_BYTES) return { ...media, ref: null };
    const now = Date.now();
    for (const [ref, held] of s.media) if (held.expires < now) s.media.delete(ref);
    while (s.media.size >= MAX_HELD_MEDIA) s.media.delete(s.media.keys().next().value);
    const ref = randomUUID();
    s.media.set(ref, { message, expires: now + MEDIA_TTL_MS });
    return { ...media, ref };
  }

  // The bytes of one file the owner sent on this SIM. Handed over once; a file that is unknown or expired is a 404.
  async media(key, ref) {
    const s = this.sessions.get(key);
    const held = s?.media.get(ref);
    s?.media.delete(ref);
    if (!held || held.expires < Date.now()) throw new HttpError(404, "no such file");
    const data = await this.downloadMedia(held.message, s?.sock);
    if (data.length > MAX_MEDIA_BYTES) throw new HttpError(413, "file too large");
    return data;
  }

  // One text message to one number. Serialised and paced by the session's pacer; the caller must not retry on
  // failure, because a retry after a timeout could send the same message twice.
  async send(key, to, text) {
    const s = this.sessions.get(key);
    if (!s || s.status !== "connected" || !s.sock) throw new HttpError(409, "not connected");
    return s.pacer.run(async () => {
      if (!s.sock || s.status !== "connected") throw new HttpError(409, "not connected");
      const sent = await s.sock.sendMessage(`${to}@s.whatsapp.net`, { text });
      return sent?.key?.id || "";
    });
  }

  // One file to one number, with the same pacing and single-attempt rule as send(). An image goes as an image.
  async sendFile(key, to, { data, mime, filename, caption }) {
    const s = this.sessions.get(key);
    if (!s || s.status !== "connected" || !s.sock) throw new HttpError(409, "not connected");
    return s.pacer.run(async () => {
      if (!s.sock || s.status !== "connected") throw new HttpError(409, "not connected");
      const content = String(mime || "").startsWith("image/")
        ? { image: data, caption: caption || undefined }
        : { document: data, mimetype: mime || "application/octet-stream", fileName: filename, caption: caption || undefined };
      const sent = await s.sock.sendMessage(`${to}@s.whatsapp.net`, content);
      return sent?.key?.id || "";
    });
  }

  async disconnect(key) {
    const s = this.sessions.get(key);
    if (!s) return { status: "absent", phone: "" };
    s.stopped = true;
    s.status = "disconnected";
    clearTimeout(s.pairTimer);
    this.sessions.delete(key);
    try {
      s.sock?.end?.(new Error("disconnect requested"));
    } catch {
      // the socket was already closed
    }
    s.sock = null;
    this.flushNow(s);
    this.report(s, "disconnected");
    return { status: "disconnected", phone: s.phone };
  }

  async shutdown() {
    const sessions = [...this.sessions.values()];
    for (const s of sessions) {
      s.stopped = true;
      try {
        s.sock?.end?.(new Error("shutdown"));
      } catch {
        // already closed
      }
      s.sock = null;
      this.flushNow(s);
    }
    await Promise.race([Promise.all(sessions.map((s) => s.chain)), new Promise((r) => setTimeout(r, 3000))]);
  }

  scheduleFlush(s) {
    if (s.loggedOut) return;
    clearTimeout(s.flushTimer);
    s.flushTimer = setTimeout(() => this.flushNow(s), this.flushDelayMs);
    s.flushTimer.unref?.();
  }

  // The full state is snapshotted now, so a flush always holds keys that were current when it was scheduled.
  flushNow(s) {
    clearTimeout(s.flushTimer);
    s.flushTimer = null;
    if (s.loggedOut) return;
    const auth = serializeState({ creds: s.creds, keys: s.keyStore.keys });
    this.enqueue(s, "state", { tenant_id: s.key, auth });
  }

  report(s, status) {
    if (s.reportedStatus === status) return;
    s.reportedStatus = status;
    this.enqueue(s, "status", { tenant_id: s.key, status, phone: s.phone });
  }

  enqueue(s, path, body) {
    s.chain = s.chain.then(() => this.callback(path, body)).catch(() => {});
    return s.chain;
  }
}
