# Smara production-gate status

Updated 2026-08-22 after the staging rollout of `9a89dd4` and the migration
lock fix. This is an operational checklist, not a claim that `ai.syntarus.com`
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
  endpoint. The Cloudflare edge is present; the exact account-level WAF rule
  configuration still needs an authenticated Cloudflare review.
- A staging custom-format Postgres dump was checksum-verified, read by
  `pg_restore`, and restored into a disposable Postgres 16 container. The
  restore contained all 14 recorded Smara schema versions. The live database
  was not modified.
- Repository tests pass in the clean Docker environment (64 tests, with only
  the existing short-JWT and bind-mounted-cache warnings).
- Account export/deletion, approval gates, bounded sandbox recipes, safe
  integration writes, dead-letter retention, and Syntarus SDK-only memory
  access are covered by the repository tests.

## Blocked until deployment credentials/operations are supplied

- The staging environment is missing `SMARA_CLI_TOKEN_SECRET`,
  `SMARA_INTEGRATION_MASTER_KEYS`, `SMARA_SENTRY_DSN`, and VAPID key pair.
  The full configuration gate intentionally fails until these are stored in a
  real secret manager and injected into every replica.
- The tested backup is on the VM for the disposable drill. A scheduled,
  encrypted **off-host** destination and a retention policy still need to be
  selected and configured.
- Cloudflare edge presence is verified, but a distributed WAF/rate-limit rule
  has not been changed or independently verified because no Cloudflare API
  credentials are available in this repository.
- Syntarus server-side metadata-filter enforcement is not yet deployed. Smara
  continues to label workspace/status filters advisory; it must not be called
  secure cross-project isolation.
- A real Windows paired-desktop smoke task, production Sentry event, and VAPID
  push delivery require their corresponding deployment credentials/devices.

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
