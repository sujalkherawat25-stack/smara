// Posts events from the sidecar back to the Smara app (state, status, inbound). Retries transient failures; a 4xx is
// the app refusing, so it is not retried. Bodies are never logged: they can hold credentials or customer text.
export function makeCallback({
  baseUrl,
  token,
  log = () => {},
  fetchImpl = globalThis.fetch,
  retries = 4,
  backoffMs = 1000,
  sleep = (ms) => new Promise((r) => setTimeout(r, ms)),
}) {
  const root = baseUrl.replace(/\/+$/, "");
  return async function post(path, body) {
    let lastError = "unknown";
    for (let attempt = 0; attempt < retries; attempt++) {
      try {
        const res = await fetchImpl(`${root}/${path}`, {
          method: "POST",
          headers: { "content-type": "application/json", authorization: `Bearer ${token}` },
          body: typeof body === "string" ? body : JSON.stringify(body),
          signal: AbortSignal.timeout(10_000),
        });
        if (res.ok) return true;
        lastError = `HTTP ${res.status}`;
        if (res.status >= 400 && res.status < 500) break;
      } catch (err) {
        lastError = err?.name || "network error";
      }
      await sleep(backoffMs * 2 ** attempt);
    }
    log("warn", `callback ${path} not delivered: ${lastError}`);
    return false;
  };
}
