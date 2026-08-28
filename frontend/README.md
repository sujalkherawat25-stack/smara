# Smara Web UI

This directory contains the reused Memento frontend shell. It is intentionally
UI-only: the MemoryOS backend, Memento agent loop, and storage clients are not
copied here.

## Hosted bridge mode

Set these values when testing the migrated shell against a Smara deployment:

```text
VITE_SMARA_MODE=true
VITE_SMARA_API_URL=/smara-api
VITE_SMARA_CONTROL_TOKEN_PATH=/v1/auth/control-token
```

Serve this bundle from the canonical `https://ai.syntarus.com/` origin. The
Smara API remains available at the same-origin `/smara-api` prefix; this is an
API route, not a second UI. The existing auth service
must have `POST /v1/auth/control-token` enabled and share
`SMARA_CONTROL_BRIDGE_SECRET` with the Smara API. The endpoint returns a
short-lived JWT (`expires_in_seconds`, bounded to 30–300 seconds); the browser
never receives the signing secret.

The browser first uses the existing authenticated web session to obtain a
short-lived Smara control token, then sends it as a bearer token to Smara. No
gateway or provider secret is shipped to the browser. With `VITE_SMARA_MODE`
unset, the shell keeps its existing Memento routes, so the public site remains
unchanged during migration and rollback.

The first migrated slices are chat streaming and task listing/cancellation.
Research evidence, approvals, schedules, integrations, desktop, and memory
screens remain on their existing routes until each slice is adapted and tested.

The build uses `vite.config.mjs` explicitly. This avoids a Windows/esbuild
configuration-loader failure seen when resolving the copied TypeScript config
from the monorepo parent path.
