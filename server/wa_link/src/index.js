// Smara WhatsApp link sidecar. Internal only: see README.md. Baileys is unofficial; see the PR notes for the risks.
import http from "node:http";
import pino from "pino";
import { makeCallback } from "./callbacks.js";
import { makeHandler } from "./http.js";
import { loadConfig } from "./config.js";
import { SessionManager } from "./sessions.js";

function log(level, msg) {
  // Plain lines with no payloads: no keys, QR codes, phone numbers or message text.
  console.log(JSON.stringify({ at: new Date().toISOString(), level, msg }));
}

let config;
try {
  config = loadConfig(process.env);
} catch (err) {
  log("fatal", err.message);
  process.exit(1);
}

const logger = pino({ level: "silent" }); // Baileys' own logs can include key material, so they are dropped
const callback = makeCallback({ baseUrl: config.callbackUrl, token: config.token, log });
const manager = new SessionManager({ callback, logger, sendGapMs: config.sendGapMs, log });
const server = http.createServer(makeHandler({ manager, token: config.token, log }));

server.listen(config.port, "0.0.0.0", () => log("info", `wa-link listening on :${config.port}`));

async function shutdown() {
  log("info", "shutting down");
  await manager.shutdown();
  server.close(() => process.exit(0));
  setTimeout(() => process.exit(0), 2000).unref();
}
process.on("SIGTERM", shutdown);
process.on("SIGINT", shutdown);
