# Shared session protocol

The CLI and Desktop now use a versioned thread, turn and item contract on top of the existing runtime SQLite database. Original session records and the bounded legacy event cursor remain compatible. Protocol events have their own durable cursor and are not deleted by legacy event retention.

## Clients

Run `smara --workspace <folder> app-server` for the long-lived local JSONL process. Send one JSON object per line. Stdout contains protocol responses and notifications; agent console output goes to stderr. Call `initialize` first.

```json
{"id":1,"method":"initialize"}
{"id":2,"method":"thread/create","params":{"thread_id":"my-chat"}}
{"id":3,"method":"turn/start","params":{"thread_id":"my-chat","request":"Inspect README.md","approval_mode":"ask","tool_profile":"coding"}}
{"id":4,"method":"thread/events","params":{"thread_id":"my-chat","after":0,"limit":100}}
```

The server responds with the same request ID. `turn/start` returns promptly, then emits `session/event` notifications while its worker runs. Up to four threads can run concurrently. One active turn per thread is enforced in SQLite. A later turn receives the thread's recent completed user and agent messages as context.

`smara --workspace <folder> session <method>` also accepts a params object on stdin for read/replay/interrupt and approval responses. Desktop uses the same dispatcher through its bundled executor. Its Run Center selects Desktop chats or the selected workspace's CLI runs, and its chat and Run Center show action approval controls.

Methods: `initialize`, `thread/create`, `thread/list`, `thread/read`, `thread/resume`, `thread/events`, `turn/start` (app server), `turn/resume` (app server and Desktop), `turn/interrupt`, `approval/list`, `approval/respond`. The Desktop polls the small approval-only response while waiting, avoiding repeated transfers of large message transcripts. `thread/resume` reconnects to durable state and does not execute a tool.

Explicit `turn/resume`, the Run Center's **Resume checkpoint** button and CLI
`smara resume` continue the original canonical execution. Thread IDs remain
stable while protocol turn IDs change. Recovery preserves the original
objective, tool profile, budget and spent usage. It requires the original
workspace and rejects live owners, cancelled/completed runs, unfinished
processes and uncertain admitted mutations. Receipts are supplied as context;
completed writes are not replayed automatically. The agent must reinspect
before making a new change. Failed recovery retains the execution pointer.
**Retry in Chat** remains a separate, new task rather than a budget reset
disguised as recovery.

## Events and approvals

Events include a version, thread ID, turn ID, sequence, kind, payload and timestamp. Tool executions and messages have stable item IDs. Items emit `item.started`, optional `item.delta`, then `item.completed`. `turn.completed` includes a final status, including failed, cancelled or needs-input outcomes. Read events in pages until `has_more` is false; use the returned protocol cursor, never the legacy runtime cursor.

With `approval_mode=ask`, tools outside the explicit read-only set pause before dispatch. Each approval binds its ID to a thread, turn, item and SHA-256 of the exact action. Reply with all four identifiers and `decision` set to `allow` or `deny`:

```json
{"id":5,"method":"approval/respond","params":{"thread_id":"my-chat","turn_id":"<turn ID>","approval_id":"<approval ID>","action_sha256":"<hash from approval.requested>","decision":"allow"}}
```

Approval previews redact recognized secret fields and API key strings. The executable action stays in the worker's memory. Approval rows are never executed on journal replay. Denied, cancelled, expired, mismatched and inactive approvals cannot dispatch the action. Approval waits expire after five minutes. CLI `run --approval-mode ask` prompts on an interactive terminal and denies unattended requests; app-server clients answer through the protocol. Desktop follows the configured ask/auto preference.

Cancellation invalidates pending approvals and running items. Completion writes are fenced by turn identity, so an old worker cannot overwrite the result of a newer turn. If an owning process has exited, beginning another turn marks the prior one interrupted and expires its approvals without replaying unfinished actions. Closing the app-server's stdin interrupts its workers. Reading a thread does not start work.

## Scope

Approval is not itself an OS sandbox and cannot undo an already-running
external side effect. Workspace and command policies still apply after
approval; command isolation is described in TERMINAL_SANDBOX.md. Desktop and
workspace ledgers remain in their existing locations. Recovery is explicit
and fails closed when mutation state is uncertain; unattended recovery of all
partially executed workflows is not promised.
