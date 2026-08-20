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

For local development, leave `SMARA_DATABASE_URL` empty and set
`SMARA_DATABASE_PATH=./data/smara.db`.
Production must use a Postgres-backed store before multiple API/worker replicas
are deployed. Redis is for event fan-out only, never the sole task source of
truth.

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
