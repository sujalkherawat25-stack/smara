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
voice, and photo captures create inbox tasks; media is retained in Smara and
never sent to an LLM automatically.

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
smara --api http://127.0.0.1:8080 --dev-account acct_local run "Prepare a cited report"
smara --api http://127.0.0.1:8080 --dev-account acct_local task watch task_xxx
# After Smara Web/Memento displays a one-time pairing code:
smara --api https://smara.example.com login smara_<one-time-code>
smara --api https://smara.example.com --token <cli-token> tasks
```

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

This slice does not use an LLM to infer claims or deliver reports externally.
Those remain separate, approval-gated capabilities. An optional
`SMARA_RESEARCH_SYNTHESIS_ENABLED=true` setting enables bounded synthesis using
the configured OpenAI-compatible model, but only after evidence is verified.
Every model citation must match the ledger; invalid, unavailable, or
unconfigured synthesis safely falls back to the deterministic cited report.

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

Use `scripts/backup-postgres.sh` for a nightly encrypted-storage database
backup and `scripts/verify-backup.sh backup.dump` to validate an archive. A
release is not complete until a documented restore is exercised into a fresh,
disposable Postgres database.

`smara.sandbox` is an internal-only recipe for future approved code/repository
steps. It uses a fresh Docker container with no network, no mounts, no inherited
environment, a read-only root filesystem, dropped capabilities and bounded CPU,
memory, process count and lifetime. It is not available through a public API.
