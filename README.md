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

## First research workflow

`POST /v1/research` accepts a question and one to twelve explicitly supplied
public HTTP(S) source URLs. The worker retrieves only safe public text/HTML
sources, records failures rather than inventing content, hashes each retrieved
source, verifies the usable evidence, and writes a cited Markdown artifact.

- `GET /v1/research/{task_id}/evidence` returns the durable evidence ledger.
- `GET /v1/tasks/{task_id}/artifacts` returns the cited report artifact.

This first slice does not discover sources automatically, use an LLM to infer
claims, or deliver reports externally. Those are separate, approval-gated
capabilities.

## Integrations and approvals

Connections are account-scoped and have one policy: `observe`, `draft`,
`assisted`, `trusted`, or `blocked`. External actions always create a durable
intent and visible preview first. Until a bounded trusted-workflow template is
added, even `trusted` external actions require approval. An approver can edit
the preview and action payload before authorizing it.

The credential endpoint stores only Fernet-encrypted ciphertext in Postgres.
Set `SMARA_INTEGRATION_MASTER_KEY` from a production secret manager before
connecting anything. Google (Gmail, Calendar, Drive) and GitHub support OAuth
authorization-code/PKCE flows once their client IDs, client secrets, and the
public callback URL are configured. Telegram uses an explicitly stored bot
token. Supported initial actions are Gmail send/search, Calendar create/list,
Telegram send, GitHub repository list/content commit, and Drive search.

No provider credential or OAuth application secret is included in this
repository. That is intentional: configure them in the deployment environment
before using a live connection.
