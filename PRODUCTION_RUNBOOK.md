# Smara production-readiness runbook

This is the final deployment layer for the independent Smara repository. It
does not change MemoryOS source, schemas, or stored memories. Do not switch
`ai.syntarus.com` until every required gate is green.

## 1. Secrets

Use the deployment's real secret manager and inject only operator-owned
secrets into the API, worker, scheduler, and (if explicitly enabled) the
integration-worker containers. User OAuth/API tokens and browser sessions must
stay on the paired desktop. Smara accepts either a direct variable or a file
variable, for example:

```text
SMARA_SENTRY_DSN_FILE=/run/secrets/smara_sentry_dsn
SMARA_VAPID_PUBLIC_KEY_FILE=/run/secrets/smara_vapid_public
SMARA_VAPID_PRIVATE_KEY_FILE=/run/secrets/smara_vapid_private
```

The default hosted posture is:

```text
SMARA_HOSTED_USER_INTEGRATIONS_ENABLED=false
```

In this mode the integration worker remains idle, personal integration tools
are absent from the agent catalogue, and integration credential/OAuth routes
return a clear local-only response. Only an explicitly reviewed migration may
set the flag to `true`; that mode additionally requires the encrypted
integration key ring.

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

The Memento/MemoryOS auth service now exposes `POST /v1/auth/control-token`.
It validates the existing httpOnly account session and returns a 30–300 second
JWT containing only the account subject, Smara audience, and issuer. Configure
the same independently generated `SMARA_CONTROL_BRIDGE_SECRET` in the auth
service and Smara API secret managers; never put it in frontend variables.
Set the migrated frontend to bridge mode and expose it first at the reversible
`/smara/` path. Proxy `/smara-api/*` to the Smara API with the prefix stripped;
the browser still obtains its token from `/v1/auth/control-token` on the
authenticated `ai.syntarus.com` origin with `credentials: include`. Test chat,
tasks, research, refresh, and approval from the same signed-in account before
considering a root-path cutover. This repository must not modify the protected
MemoryOS pipeline.

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
