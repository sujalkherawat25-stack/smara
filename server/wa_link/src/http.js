// The internal HTTP API. Only the Smara app calls it (bearer token, compose network only; no port is published).
import { timingSafeEqual } from "node:crypto";
import { HttpError } from "./sessions.js";

const KEY_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const REF_RE = KEY_RE;   // a media ref is a random UUID made by the sidecar
const TO_RE = /^\d{8,15}$/;
const TEXT_MAX = 4000;

async function readJson(req, limit) {
  const chunks = [];
  let size = 0;
  for await (const chunk of req) {
    size += chunk.length;
    if (size > limit) throw new HttpError(413, "body too large");
    chunks.push(chunk);
  }
  try {
    const parsed = JSON.parse(Buffer.concat(chunks).toString("utf8") || "{}");
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("not an object");
    return parsed;
  } catch {
    throw new HttpError(400, "bad json");
  }
}

export function makeHandler({ manager, token, maxBodyBytes = 8 * 1024 * 1024, log = () => {} }) {
  const expected = Buffer.from(`Bearer ${token}`);
  const authorized = (req) => {
    const got = Buffer.from(req.headers.authorization || "");
    return got.length === expected.length && timingSafeEqual(got, expected);
  };

  return async function handle(req, res) {
    const reply = (status, body) => {
      res.writeHead(status, { "content-type": "application/json", "cache-control": "no-store" });
      res.end(JSON.stringify(body));
    };
    try {
      const url = new URL(req.url || "/", "http://internal");
      if (req.method === "GET" && url.pathname === "/healthz") {
        return reply(200, { ok: true, sessions: manager.count() });
      }
      if (!authorized(req)) return reply(401, { error: "unauthorized" });

      const parts = url.pathname.split("/").filter(Boolean);
      if (parts[0] !== "sessions" || !KEY_RE.test(parts[1] || "")) return reply(404, { error: "not found" });
      const key = parts[1].toLowerCase();
      const action = parts[2] || "";

      if (req.method === "GET" && !action) return reply(200, await manager.status(key));

      if (req.method === "GET" && action === "media") {       // one file the owner sent on this SIM; handed over once
        const ref = parts[3] || "";
        if (!REF_RE.test(ref)) return reply(404, { error: "not found" });
        const data = await manager.media(key, ref);
        res.writeHead(200, { "content-type": "application/octet-stream", "cache-control": "no-store", "content-length": data.length });
        return res.end(data);
      }

      if (req.method === "POST" && action === "pair") {
        const body = await readJson(req, maxBodyBytes);
        const auth = body.auth ?? null;
        if (auth !== null && typeof auth !== "string") throw new HttpError(400, "auth must be a string");
        return reply(200, await manager.start(key, auth));
      }

      if (req.method === "POST" && action === "send") {
        const body = await readJson(req, 64 * 1024);
        if (typeof body.to !== "string" || !TO_RE.test(body.to)) throw new HttpError(400, "to must be digits only");
        if (typeof body.text !== "string" || !body.text.trim() || body.text.length > TEXT_MAX) {
          throw new HttpError(400, "text must be 1-4000 characters");
        }
        let messageId;
        try {
          messageId = await manager.send(key, body.to, body.text);
        } catch (err) {
          if (err instanceof HttpError) throw err;
          log("warn", "send failed");   // the reason is not echoed: it can carry message or account details
          throw new HttpError(502, "send failed");
        }
        return reply(200, { message_id: messageId });
      }

      if (req.method === "POST" && action === "disconnect") return reply(200, await manager.disconnect(key));

      return reply(404, { error: "not found" });
    } catch (err) {
      if (err instanceof HttpError) return reply(err.status, { error: err.message });
      log("error", "request failed");
      return reply(500, { error: "internal error" });
    }
  };
}
