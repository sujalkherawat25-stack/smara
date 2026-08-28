# Smara production-gate status

Updated 2026-08-28 after the canonical Smara route cleanup. This is an
operational checklist: Smara is live at the root as a beta deployment, the
same-origin `/smara-api` route is retained for clients, and Memento rollback is
preserved without a second public UI.

## Passed

- Staging API, worker, scheduler, integration worker, Postgres, and Redis are
  running under the original `smara-staging` Compose project.
- `/health` and `/readyz` pass locally and through the canonical
  `https://ai.syntarus.com/smara-api` endpoint.
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
- A bounded burst through the public hostname with a short-lived synthetic
  signed account returned 120 successful tool-catalogue responses followed by
  5 HTTP 429 responses, confirming authenticated limiter behavior without
  printing a credential.
- Cloudflare/Caddy/TLS/security headers are visible on the staging public
  endpoint. The narrowly scoped CLI device WAF rule is verified through the
  public request/approval/poll path; rate limiting remains enabled outside it.
- A staging custom-format Postgres dump was checksum-verified, read by
  `pg_restore`, and restored into a disposable Postgres 16 container. The
  2026-08-25 restore contained all 17 recorded Smara schema versions and 17
  staging tasks. The live database was not modified; backup files are now
  forced to owner-only mode by the script.
- Repository tests pass locally (**119 tests**) with Python compilation,
  frontend type-check, and production frontend build. The lease-heartbeat
  change is now deployed and the live Postgres desktop claim/heartbeat/
  completion smoke passed. The Work panel also now maps the deployed
  `type`/`payload` task-event contract, shows the final task result, and
  expands evidence and artifact details instead of rendering timestamps only.
  The frontend-only deployment was rebuilt and all Smara services remained
  running.
- Durable task results are now part of the task API contract. A live
  account-scoped task smoke after deployment returned its final text through
  `GET /v1/tasks/{id}` (rather than the bookkeeping value `recorded`), and
  migration `018_task_result_summary.sql` is applied with **18 applied / 0
  pending**. Legacy rows are backfilled when a meaningful step result exists.
- A fresh live backup was checksum-verified and restored into a disposable
  Postgres 16 container. The restore contained both task tables and all **18**
  recorded Smara migrations; the temporary container and archive were removed
  after the drill. This validates restore mechanics but is not an off-host
  backup schedule.
- Account export/deletion, approval gates, bounded sandbox recipes, safe
  integration writes, dead-letter retention, and Syntarus SDK-only memory
  access are covered by the repository tests.
- Sandbox execution is fail-closed in the live worker: `SMARA_SANDBOX_ENABLED`
  defaults to false, and a disabled deployment does not claim sandbox steps.
  This avoids the unsafe shortcut of mounting the host Docker socket.
- Hosted personal integrations are now fail-closed by policy:
  `SMARA_HOSTED_USER_INTEGRATIONS_ENABLED=false` removes their tools/plugins,
  rejects credential/OAuth/action writes, and keeps the integration worker from
  claiming or decrypting old rows. Browser, file, terminal, and future private
  integrations are local-desktop work; the VM retains only coordination,
  monitoring, operator-owned provider keys, and public research.
- This policy is deployed on VM commit `0775f71`: all seven services are
  running, migrations report **18 applied / 0 pending**, the public Smara UI/API
  and unchanged root route return 200, signed `/v1/tools` and `/v1/plugins`
  omit personal integrations, `/v1/integrations` reports `mode: local-only`,
  and a credential-write probe returns HTTP 409 without storing the test value.
- Smara serves `https://ai.syntarus.com/` as the public root. `/v1/*` continues
  to proxy to the existing Memento auth/legacy backend for the session bridge,
  while `/smara-api/*` proxies to Smara. The old `/smara/` route now returns
  `410 Gone`, `/smara-api/app/` is absent, and the `control-staging.syntarus.com`
  DNS record/service is retired. A dated Caddy rollback copy remains available.
- The repeatable signed beta smoke passed on staging: public health/readiness,
  signed tool access, disposable task create/cancel, cross-account task
  isolation (404), and local-only integration policy. No secret values were
  printed and no user task was modified.
- The bounded authenticated limiter review returned **120 HTTP 200** responses
  followed by **5 HTTP 429** responses. This confirms the backend Redis window;
  a separate Cloudflare-wide distributed policy review remains open.
- The native desktop release was stopped and restarted successfully after the
  UX build. The focused desktop/auth/provider suite passed (**26 tests**), and
  the supported unsigned NSIS package rebuilt successfully.

## Deferred by owner for the current beta

The following hardening gates are intentionally postponed. Their safe defaults
remain in force and they must be reopened before a later production promotion:

- Sentry DSN and alert delivery.
- Authenticode signing and a trusted Windows update channel.
- External VM secret manager and live rotation drill.
- Encrypted off-host backup scheduling and recurring restore drill.
- Hosted sandbox deployment. `SMARA_SANDBOX_ENABLED=false` remains enforced;
  private browser/file/terminal work stays on the paired desktop.

## Other open verification items

- The staging integration key ring, CLI signing secret, Syntarus key, and VAPID
  key pair are configured. VAPID's private key is a protected read-only host
  mount for staging. External secret-manager migration and rotation are
  intentionally deferred, and `SMARA_SENTRY_DSN` remains unset by decision.
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
- A Windows paired-desktop smoke task already passed. The local process restart
  check is green; a real paired task across PC disconnect/reconnect still needs
  to be run with the owner's paired account. A production Sentry event and real
  phone VAPID delivery still require their corresponding deployment
  credentials/device subscription.
- The Docker sandbox command is not yet a live hosted executor. Before enabling
  `SMARA_SANDBOX_ENABLED`, deploy a separate sandbox service with its own
  runtime, resource limits, network policy, and request authentication.
  The worker now supports that narrow remote `/v1/run` contract and the full
  configuration gate rejects an enabled sandbox without both URL and token.

## Desktop UX verification — 2026-08-28

- Settings now exposes hosted Automatic/Grok/Sarvam profiles as selectable
  provider cards. Only the profile name crosses the desktop/hosted boundary;
  provider keys remain operator-managed on the hosted service.
- Desktop sign-in now repairs legacy installs that saved the domain root and
  opens the real Smara approval shell at `/?cli_device=...`; the native
  app polls the same request and stores the resulting device token locally.
- Settings now includes an **Add provider** dialog for private Sarvam, Grok,
  and custom OpenAI-compatible chat endpoints. The Sarvam preset uses the
  documented chat endpoint/model/header. Keys are encrypted to the Windows
  account, and selecting a private profile enables direct desktop chat without
  requiring hosted sign-in; hosted tasks remain hosted and approval-gated.
- Tavily, GitHub, and custom local credentials have visible configured/not
  configured status. Local secrets remain encrypted on Windows and are only
  injected into an approved process through the existing executor contract.
- Files, terminal, and browser allowlists show live entry counts and On/Off
  state. The UI explains that an allowlist is eligibility, not approval.
- A failed connection check now clears stale remote-online state, and a native
  chat event subscription failure is surfaced instead of being silently lost.
- `npm run build`, 122 backend tests, and `cargo check` passed. The NSIS beta
  installer was rebuilt and the release app restarted successfully.
- The installer includes a Windows Desktop shortcut hook (`Smara Desktop.lnk`)
  with matching uninstall cleanup. The current shortcut target was checked and
  resolves to the running release executable.
- A local integration adapter (Gmail/Calendar/Drive/GitHub/Telegram) is still
  future work. The hosted API deliberately does not accept those user secrets
  while that adapter is unfinished.
- Interactive CLI sessions, allowlisted model profiles, declarative plugin
  catalogue, and approval-gated desktop action requests are covered by the
  current repository tests. They do not by themselves configure provider keys,
  a real MCP server, or a Windows desktop.

## Cutover rule

The reversible beta root switch is active. Do not delete or stop the existing
Memento backend until authenticated shadowing, off-host encrypted backups,
operator-secret rotation, edge-rate-limit review, and Windows executor tests
are green. If a regression appears, restore
`/etc/caddy/Caddyfile.pre-smara-root-cutover-20260827`, validate, and reload
Caddy. MemoryOS source, schemas, and stored memories remain untouched.

Run the repository checker in the deployed image:

```sh
./scripts/check-production-config.sh --full
```
