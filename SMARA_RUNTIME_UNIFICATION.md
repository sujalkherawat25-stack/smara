# Smara Runtime Unification

Status: implemented and installed (2026-09-18)

## What is now shared

CLI, Desktop, loopback transport, and hosted API use the same versioned
runtime-session envelope and append-only event ledger:

- session identity, request, workspace, model/tool/research profiles;
- lifecycle status (`created`, `running`, `waiting_approval`, `completed`,
  `needs_input`, `failed`, `cancelled`);
- bounded result/unresolved work and a monotonic event cursor;
- cooperative cancellation with cancellation winning late-completion races;
- restart-safe replay from `after`/`cursor`, with an explicit replay-gap error;
- resume is idempotent for terminal results and emits a durable resume marker for
  resumable sessions.

The SQLite schema is migrated in place for existing Desktop state databases.
CLI workspaces store the same schema at `.smara/runtime-sessions.sqlite3`;
the packaged Desktop stores it beside its AppData state file; the hosted API
stores it beside the configured server database. These are deployment-local
ledgers with one wire contract, so a client can reconnect without depending on
the original process.

## Client boundaries

Hosted API:

- `GET /v1/runtime-sessions/{id}?after=N&limit=M`
- `GET /v1/runtime-sessions/{id}/events?after=N&limit=M`
- `POST /v1/runtime-sessions/{id}/cancel`
- `POST /v1/runtime-sessions/{id}/resume`
- `/v1/chat/stream` persists and annotates each safe SSE event with
  `session_id` and `sequence`.

Desktop:

- bundled executor flags `--runtime-session-detail`,
  `--runtime-session-cancel`, and `--runtime-session-resume`;
- Tauri commands `get_runtime_session`, `cancel_runtime_session`, and
  `resume_runtime_session`;
- local-agent completion events carry the canonical session ID/cursor;
- the UI rehydrates from the runtime snapshot and exposes a real Cancel action.

CLI/loopback:

- run/resume/cancel/inspect publish the runtime envelope;
- loopback `run`, `resume`, `cancel`, and `inspect` include `runtime` and
  `event_cursor` while preserving the existing SessionEngine journal for
  specialised evidence and artifacts.

## Verification

- Python regression: **913 passed, 1 skipped**.
- Desktop TypeScript/Vite build: passed.
- Tauri `cargo check`: passed.
- Installed app responds after replacement.
- Installed bundled executor replay/cancel smoke: passed.

Installed binary hashes:

- `Smara Desktop.exe`: `71C4F9AEECB7D969463DF66205F748DFB7424D9E44ACB80FFBC1492193FC6FAB`
- bundled executor: `A2A6FD0AD39F98BEA352135058E4F2F0643EBDFC3448A6ADAF6C43786BCD208B`

## Operational note

Provider credentials remain independent of this runtime boundary. If a model
key is unavailable, the session is recorded as `needs_input` with an explicit
unresolved item; no client reports a false completion.
