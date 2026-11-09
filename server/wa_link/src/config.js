// Settings come from the environment only. Nothing here is written to disk, and errors never echo a secret.
import { readFileSync } from "node:fs";

// WA_LINK_TOKEN, or the contents of the file named by WA_LINK_TOKEN_FILE (a Docker secret). Never printed.
function secret(env, name) {
  if (env[`${name}_FILE`]) return readFileSync(env[`${name}_FILE`], "utf8").trim();
  return env[name] || "";
}

export function loadConfig(env) {
  const token = secret(env, "WA_LINK_TOKEN");
  if (token.length < 32) throw new Error("WA_LINK_TOKEN must be set to at least 32 characters");
  const callbackUrl = env.WA_LINK_CALLBACK_URL || "";
  if (!/^https?:\/\/[^/\s]+/.test(callbackUrl)) {
    throw new Error("WA_LINK_CALLBACK_URL must be the Smara app's internal base URL, e.g. http://app:8080/internal/wa-link");
  }
  const port = Number(env.WA_LINK_PORT || 8090);
  const sendGapMs = Number(env.WA_LINK_SEND_GAP_MS || 3000);
  if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error("WA_LINK_PORT is not a port number");
  if (!Number.isFinite(sendGapMs) || sendGapMs < 0) throw new Error("WA_LINK_SEND_GAP_MS must be zero or more");
  return { token, callbackUrl, port, sendGapMs };
}
