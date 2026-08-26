# Smara production-gate status

Updated 2026-08-27 after the desktop lease reliability audit. This is an operational
checklist, not a claim that `ai.syntarus.com`
has been cut over.

## Passed

- Staging API, worker, scheduler, integration worker, Postgres, and Redis are
  running under the original `smara-staging` Compose project.
- `/health` and `/readyz` pass locally and through
  `https://control-staging.syntarus.com`.
- The tested Smara Caddy configuration is now installed at `/etc/caddy/Caddyfile`
  after validation, with `/etc/caddy/Caddyfile.pre-smara-20260827` retained for
  rollback; Caddy reloaded and then restarted successfully, with `/readyz`
  still returning 200.
- Signed gateway authentication was exercised in the staging API container;
  an account-scoped request received the read-only tool catalogue.
- The Postgres migration runner now serializes concurrent service startup with
  an advisory lock. A clean staging restart produced no duplicate-migration
  error.
- The Redis-backed fixed-window limiter is enabled when `SMARA_REDIS_URL` is
  present; production refuses the local-only fallback.
- Cloudflare/Caddy/TLS/security headers are visible on the staging public
  endpoint. The narrowly scoped CLI device WAF rule is verified through the
  public request/approval/poll path; rate limiting remains enabled outside it.
- A staging custom-format Postgres dump was checksum-verified, read by
  `pg_restore`, and restored into a disposable Postgres 16 container. The
  2026-08-25 restore contained all 17 recorded Smara schema versions and 17
  staging tasks. The live database was not modified; backup files are now
  forced to owner-only mode by the script.
- Repository tests pass locally (**113 tests**) with Python compilation,
  frontend type-check, and production frontend build. The lease-heartbeat
  change is now deployed and the live Postgres desktop claim/heartbeat/
  completion smoke passed.
- Account export/deletion, approval gates, bounded sandbox recipes, safe
  integration writes, dead-letter retention, and Syntarus SDK-only memory
  access are covered by the repository tests.
- Sandbox execution is fail-closed in the live worker: `SMARA_SANDBOX_ENABLED`
  defaults to false, and a disabled deployment does not claim sandbox steps.
  This avoids the unsafe shortcut of mounting the host Docker socket.

## Blocked until deployment credentials/operations are supplied

- The staging integration key ring, CLI signing secret, Syntarus key, and VAPID
  key pair are configured. VAPID's private key is a protected read-only host
  mount for staging; all of these still need the production secret-manager and
  rotation drill. `SMARA_SENTRY_DSN` remains missing, so the full gate fails.
- Staging xAI `grok-4.3` and Tavily search are configured. Live checks passed
  for a Grok response, Tavily discovery and page retrieval, a calculator tool
  turn, an end-to-end research turn with a fetched citation URL, and the
  Postgres desktop approval/claim/heartbeat/completion workflow.
- The tested backup is on the VM for the disposable drill. A scheduled,
  encrypted **off-host** destination and a retention policy still need to be
  selected and configured.
- The CLI WAF rule is scoped to the device endpoints; a separate distributed
  rate-limit policy for all authenticated API traffic still needs an
  authenticated Cloudflare review.
- Syntarus server-side metadata-filter enforcement is not yet deployed. Smara
  continues to label workspace/status filters advisory; it must not be called
  secure cross-project isolation.
- A Windows paired-desktop smoke task already passed. A production Sentry event
  and real phone VAPID delivery still require their corresponding deployment
  credentials/device subscription.
- The Docker sandbox command is not yet a live hosted executor. Before enabling
  `SMARA_SANDBOX_ENABLED`, deploy a separate sandbox service with its own
  runtime, resource limits, network policy, and request authentication.
  The worker now supports that narrow remote `/v1/run` contract and the full
  configuration gate rejects an enabled sandbox without both URL and token.
- Interactive CLI sessions, allowlisted model profiles, declarative plugin
  catalogue, and approval-gated desktop action requests are covered by the
  current repository tests. They do not by themselves configure provider keys,
  a real MCP server, or a Windows desktop.

## Cutover rule

Do not replace the existing `ai.syntarus.com` MemoryOS/Memento application yet.
First inject the missing secrets, configure an off-host encrypted backup, verify
the gateway/WAF rules, run the full configuration gate and live shadow tests,
then perform a reversible proxy switch with the previous deployment retained
for rollback. MemoryOS source, schemas, and stored memories remain untouched.

Run the repository checker in the deployed image:

```sh
./scripts/check-production-config.sh --full
```
