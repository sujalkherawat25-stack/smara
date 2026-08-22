# Smara

Smara is the independent agent control plane. It owns tasks, approvals, task
events, executors and integrations. Long-term memory is accessed **only** via
the public `syntarus` SDK; this repository must never import MemoryOS internals
or connect to Qdrant/Neo4j directly.

## Services

- `api`: task and approval HTTP API.
- `worker`: claims durable task steps and invokes an executor.
- `scheduler`: creates due task runs (the initial implementation is deliberately
  small; recurring schedules are added through the same task API).
- `integration-worker`: executes only approved, idempotent provider actions;
  it cannot run until the credential vault key is configured.

For local development, leave `SMARA_DATABASE_URL` empty and set
`SMARA_DATABASE_PATH=./data/smara.db`.
Production must use a Postgres-backed store before multiple API/worker replicas
are deployed. Redis is for event fan-out only, never the sole task source of
truth.

## Control app and phone companion

The task-first Smara control app is served by the API at `/app/` (or `/`). It
works responsively on a phone and is installable as a small PWA companion for
task status and approvals. It does not run a phone executor. In development,
enter a local account identifier in the sidebar; production requests rely on
the signed identity gateway and do not expose an account identifier in browser
storage.

Phone alerts need HTTPS plus the VAPID keys in `.env.example`. Once enabled,
the installed app can register its own browser-push subscription and Smara
sends an approval alert when an external action enters review. Quick text,
voice, and photo captures create inbox tasks. Media is retained in Smara and is
never sent to a provider unless the deployment explicitly configures the
matching capture provider. With `SMARA_CAPTURE_TRANSCRIPTION_*` configured,
voice creates a bounded transcript artifact; with
`SMARA_CAPTURE_VISION_*` configured, photos create a bounded description
artifact. Without those settings the task completes locally with a clear
"not configured" result, so a missing optional provider cannot block the phone
companion.

## Memory boundary

`src/smara/syntarus_adapter.py` is the only memory integration point. Give the
service a server-side Syntarus project key; never send it to web, phone, or
desktop clients.

## CLI and extracted agent runtime

`smara` is a thin client of this API, not a second local agent or memory store.
It is the first migration surface for Memento's agent behaviour and lets us
test the same task graph used by Web, Desktop, and Phone:

```powershell
smara --api http://127.0.0.1:8080 --dev-account acct_local tasks
smara --api http://127.0.0.1:8080 --dev-account acct_local tasks list
smara --api http://127.0.0.1:8080 --dev-account acct_local run "Prepare a cited report"
smara --api http://127.0.0.1:8080 --dev-account acct_local task watch task_xxx
# After Smara Web/Memento displays a one-time pairing code:
smara --api https://smara.example.com login smara_<one-time-code>
smara --api https://smara.example.com --token <cli-token> tasks
```

`smara login <one-time-code>` saves the scoped bearer in the user CLI config
directory; use `--print-token` only when a script explicitly needs it.
`smara logout` removes that token. `task watch` resumes after a dropped SSE
connection using the last durable event ID.

`smara ask "..."` is intentionally a short direct conversation. It requires a
configured OpenAI-compatible provider (`SMARA_LLM_*`) and can retrieve context
only through the Syntarus SDK adapter. Work that takes time, needs tools,
creates an artifact, or needs approval must use `smara run` so it survives a
closed terminal or browser.

The pairing flow is deliberately two-part: an already authenticated Web or
Memento session starts the code, and the terminal exchanges it once. Smara
stores only a hash of the code; the CLI bearer uses a separate signing secret
and is accepted only for the `smara-cli` audience. A future device settings
screen will list and revoke issued CLI devices.

`POST /v1/chat/stream` provides the same direct turn as safe Server-Sent Events
for Web and CLI clients. Its stable event names (`phase`, `status`, `token`,
`done`, `error`) are adapted from Memento's proven contract. They expose useful
progress but never model chain-of-thought or raw upstream error strings.

The provider-neutral safe tool registry is available at `GET /v1/tools`. The
initial tools are UTC time, bounded arithmetic, public research search, and
SSRF-safe URL retrieval. Read-only tools may be invoked through
`POST /v1/tools/{name}`; side-effecting tools are deliberately rejected there
and must run as approved durable tasks.

The CLI is the same hosted client: `smara tools`, `smara tool calculate
--arguments '{"expression":"2+2"}'`, `smara desktop list`, and
`smara desktop pair`. It stores no task or memory database locally.

### Persistent desktop executor

The independent `smara-desktop` command is the local executor. It makes only
outbound HTTPS requests, stores its paired device token in the operating
system's Smara config directory, and waits for a hosted lease before doing
anything. Start with the least-privileged pairing:

```powershell
smara desktop pair --capability local_file_read
smara-desktop --pair ABCD1234 --api https://smara.example.com --allow-root C:\Users\you\Documents
```

The desktop currently supports bounded `local_file_read` (hash/proof by
default, content only when the approved payload explicitly requests it),
atomic `local_file_write`, allowlisted `local_terminal`, and allowlisted
`local_browser` URL opening. Terminal and browser capabilities must be
declared during pairing and configured locally with `--terminal-allow` or
`--browser-domain`; the executor rejects undeclared capabilities, unapproved
tasks, shell operators, path traversal, symlink escapes, oversized files, and
unbounded commands. `--once` is available for a single-step smoke test.

The agent manifest also includes read-only Gmail search, Calendar listing,
Drive metadata search, and GitHub repository listing. They run only when the
account has a connected credential; all external writes remain durable,
approval-gated integration actions. Inside a hosted agent task, the model may
also create an `integration.request_approval` intent; this only places an
editable preview in the approval inbox and never calls the provider itself.

## First research workflow

`POST /v1/research` accepts a question and optionally up to twelve explicitly
supplied public HTTP(S) source URLs. If no URLs are supplied, the first task
step uses the configured provider-neutral search adapter to discover public
sources before retrieval. Configure `SMARA_SEARCH_PROVIDER`,
`SMARA_SEARCH_API_KEY`, and (optionally) `SMARA_SEARCH_URL` on the server. The
provider can be `brave`, `serper`, or `tavily`; when the URL is omitted Smara
selects the provider's official endpoint. The provider key never leaves the
API process. The worker retrieves only safe public text/HTML
sources, records failures rather than inventing content, hashes each retrieved
source, verifies the usable evidence, and writes a cited Markdown artifact.

- `GET /v1/research/{task_id}/evidence` returns the durable evidence ledger.
- `GET /v1/tasks/{task_id}/artifacts` returns the cited report artifact.
- `smara research "question"` creates a research task; `smara task evidence <id>`
  prints its source ledger from PowerShell or a terminal.

Each verified source also records transparent quality signals: publication-date
presence, HTTPS use, configured domain policy, and deterministic token-level
agreement with other retrieved sources. These signals are advisory and remain
visible in the Web/CLI; they never become hidden claims or bypass approval.

Task progress is durable and can be followed live with
`GET /v1/tasks/{task_id}/events/stream`. The Web task board listens to this SSE
stream and the CLI `task watch` command uses it too. Each update has an event
ID; Web reconnects send `Last-Event-ID` so a dropped connection resumes from
the durable ledger instead of silently skipping progress.

Schedules use the same task graph rather than a second execution engine. Create
one with `POST /v1/schedules` and an `interval_seconds` value between 60 and
2,592,000; the separate scheduler process creates a normal task run when due.
Each run keeps its own approval setting, and stopping a schedule prevents future
runs without altering completed history.

This slice does not use an LLM to infer claims or deliver reports externally.
Those remain separate, approval-gated capabilities. An optional
`SMARA_RESEARCH_SYNTHESIS_ENABLED=true` setting enables bounded synthesis using
the configured OpenAI-compatible model, but only after evidence is verified.
Every model citation must match the ledger; invalid, unavailable, or
unconfigured synthesis safely falls back to the deterministic cited report.

Capture providers use independent credentials and endpoints so a speech or
vision key is never reused for ordinary agent calls. Set all three variables in
each group to enable a provider (for example, an OpenAI-compatible endpoint):
`SMARA_CAPTURE_TRANSCRIPTION_BASE_URL`,
`SMARA_CAPTURE_TRANSCRIPTION_API_KEY`,
`SMARA_CAPTURE_TRANSCRIPTION_MODEL`; or
`SMARA_CAPTURE_VISION_BASE_URL`, `SMARA_CAPTURE_VISION_API_KEY`,
`SMARA_CAPTURE_VISION_MODEL`. Upload limits remain 10 MB for voice and 4 MB for
images, and provider errors are recorded through the normal bounded retry path.

## Integrations and approvals

Connections are account-scoped and have one policy: `observe`, `draft`,
`assisted`, `trusted`, or `blocked`. External actions always create a durable
intent and visible preview first. Until a bounded trusted-workflow template is
added, even `trusted` external actions require approval. An approver can edit
the preview and action payload before authorizing it.

The credential endpoint stores only Fernet-encrypted ciphertext in Postgres.
Set `SMARA_INTEGRATION_MASTER_KEYS` from a production secret manager before
connecting anything. It is an ordered key ring: the first key encrypts and all
keys decrypt, enabling a no-downtime key rotation. Google (Gmail, Calendar, Drive) and GitHub support OAuth
authorization-code/PKCE flows once their client IDs, client secrets, and the
public callback URL are configured. Telegram uses an explicitly stored bot
token. Supported initial actions are Gmail send/search, Calendar create/list,
Telegram send, GitHub repository list/content commit, and Drive search.

No provider credential or OAuth application secret is included in this
repository. That is intentional: configure them in the deployment environment
before using a live connection.

## Operations and isolated execution

`GET /readyz` checks the task database; `/health` only confirms the process is
alive. Terminal task failures are retained in `GET /v1/dead-letters` for human
review rather than disappearing after retries. The API has an abuse limit and
safe browser headers; deploy a gateway/WAF in front of it for distributed rate
limits.

For a production schema migration, use the same image and database URL as the
services: `smara-migrate` reports applied/pending versions, while
`smara-migrate --apply` applies only pending SQL files. It never prints the
database URL or copies MemoryOS data. Run it before the API/worker rollout and
keep the previous deployment available for rollback.

Use `scripts/backup-postgres.sh` for a nightly encrypted-storage database
backup and `scripts/verify-backup.sh backup.dump` to validate an archive. A
release is not complete until a documented restore is exercised into a fresh,
disposable Postgres database.

`smara.sandbox` is an internal-only recipe for future approved code/repository
steps. It uses a fresh Docker container with no network, no mounts, no inherited
environment, a read-only root filesystem, dropped capabilities and bounded CPU,
memory, process count and lifetime. It is not available through a public API.
