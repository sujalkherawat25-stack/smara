# Smara production-gate status

Updated 2026-08-22 after the interactive CLI, desktop action bridge, provider
profiles, and sandbox service-boundary rollout. This is an operational
checklist, not a claim that `ai.syntarus.com`
has been cut over.

## Passed

- Staging API, worker, scheduler, integration worker, Postgres, and Redis are
  running under the original `smara-staging` Compose project.
- `/health` and `/readyz` pass locally and through
  `https://control-staging.syntarus.com`.
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
  restore contained all 14 recorded Smara schema versions. The live database
  was not modified.
- Repository tests pass in the clean Docker environment (76 tests, with only
  the existing short-JWT and bind-mounted-cache warnings).
- Account export/deletion, approval gates, bounded sandbox recipes, safe
  integration writes, dead-letter retention, and Syntarus SDK-only memory
  access are covered by the repository tests.
- Sandbox execution is fail-closed in the live worker: `SMARA_SANDBOX_ENABLED`
  defaults to false, and a disabled deployment does not claim sandbox steps.
  This avoids the unsafe shortcut of mounting the host Docker socket.

## Blocked until deployment credentials/operations are supplied

- The staging environment is missing `SMARA_INTEGRATION_MASTER_KEYS`,
  `SMARA_SENTRY_DSN`, and the VAPID key pair. `SMARA_CLI_TOKEN_SECRET` is
  configured and the browser device-login flow is verified.
  The full configuration gate intentionally fails until these are stored in a
  real secret manager and injected into every replica.
- The tested backup is on the VM for the disposable drill. A scheduled,
  encrypted **off-host** destination and a retention policy still need to be
  selected and configured.
- The CLI WAF rule is scoped to the device endpoints; a separate distributed
  rate-limit policy for all authenticated API traffic still needs an
  authenticated Cloudflare review.
- Syntarus server-side metadata-filter enforcement is not yet deployed. Smara
  continues to label workspace/status filters advisory; it must not be called
  secure cross-project isolation.
- A real Windows paired-desktop smoke task, production Sentry event, and VAPID
  push delivery require their corresponding deployment credentials/devices.
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
