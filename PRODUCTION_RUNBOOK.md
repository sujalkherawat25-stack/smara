# Smara production-readiness runbook

This is the final deployment layer for the independent Smara repository. It
does not change MemoryOS source, schemas, or stored memories. Do not switch
`ai.syntarus.com` until every required gate is green.

## 1. Secrets

Use the deployment's real secret manager and inject files into the API,
worker, scheduler, and integration-worker containers. Smara accepts either a
direct variable or a file variable, for example:

```text
SMARA_INTEGRATION_MASTER_KEYS_FILE=/run/secrets/smara_integration_keys
SMARA_SENTRY_DSN_FILE=/run/secrets/smara_sentry_dsn
SMARA_VAPID_PUBLIC_KEY_FILE=/run/secrets/smara_vapid_public
SMARA_VAPID_PRIVATE_KEY_FILE=/run/secrets/smara_vapid_private
```

Never commit `.env`, print secret values, or put provider keys in a task,
browser, CLI token, or log. After injection, run:

```sh
docker compose -p smara-staging exec -T api ./scripts/check-production-config.sh --full
```

## 2. Backups

Choose an encrypted off-host destination first. `BACKUP_DIR` must not be the
application container layer or an ordinary local VM directory. Run the backup
script, checksum verification, and a disposable restore drill before cutover.
Retain at least seven daily copies and test one restore on every release.

```sh
BACKUP_DIR=/path/to/encrypted/off-host/mount ./scripts/backup-postgres.sh
./scripts/verify-backup.sh /path/to/encrypted/off-host/mount/smara-<stamp>.dump
```

## 3. Edge protection

Keep the narrow CLI device WAF exception only for the device request/approval
paths. Add authenticated distributed rate limits for all `/v1/*` traffic,
verify that the rate-limit rule is ordered after the CLI exception, and run
anonymous, authenticated, and burst tests through the public hostname.

## 4. Windows desktop smoke

Pair a disposable Windows executor with only `local_file_read`, then test a
read, approval-gated write, allowlisted terminal command, browser allowlist,
disconnect/reconnect, and token revocation. Do not grant terminal or browser
capabilities in the first pairing unless explicitly needed.

## 5. Existing Smara shell

The Memento/MemoryOS frontend already obtains a short-lived Control bridge token
and sends it to the embedded Smara iframe. Set its production build variable
to the production Control hostname, verify the iframe receives the token, and
test browser CLI approval from the same signed-in account. This repository must
not modify the protected MemoryOS pipeline.

## 6. Memory isolation

Smara sends workspace and provenance metadata through the public Syntarus SDK,
but the Syntarus server must enforce those filters before this can be called a
security boundary. Run the six filter/isolation tests against the real staging
Memory API; if any fail, keep the feature advisory and do not cut over.

## 7. Shadow and cutover

Run read-only health, authentication, tool catalogue, disposable task,
research retrieval, approval, scheduler, desktop, memory, and rollback checks.
Then proxy only a beta account to Smara while keeping the existing Memento
deployment available for immediate rollback. Record the release commit,
migration status, backup hash, and rollback command.
