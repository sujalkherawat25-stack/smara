# Smara Web UI

This directory contains the Smara Web shell. It is intentionally UI-only:
Smara's API, task graph, and storage clients stay in the service container.
MemoryOS remains a separate Syntarus product and is never imported here.

## Canonical Smara mode

Set these values when testing the migrated shell against a Smara deployment:

```text
VITE_SMARA_MODE=true
VITE_SMARA_API_URL=/smara-api
```

Serve this bundle from the canonical `https://ai.syntarus.com/` origin. The
Smara API remains available at the same-origin `/smara-api` prefix; this is an
API route, not a second UI. Native Smara auth uses an HttpOnly session cookie
from `/smara-api/v1/auth/*`; no control-token bridge or provider secret is
shipped to the browser. With `VITE_SMARA_MODE` unset, local development can
still proxy `/v1` to a local API, but production should keep it enabled.

The shell covers chat, task graph, research, approvals, schedules,
integrations, desktop status, and the independent operator console at
`/admin`. The console uses its own operator-secret cookie and presents Smara
control metadata beside a bounded Syntarus health view; it does not join the
two data stores.

The build uses `vite.config.mjs` explicitly. This avoids a Windows/esbuild
configuration-loader failure seen when resolving the copied TypeScript config
from the monorepo parent path.
