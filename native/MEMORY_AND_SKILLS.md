# Smara conversation memory and procedural skills

## Two distinct stores, one native execution engine

- **Continuum by Syntarus:** hosted conversation memory through the public
  `syntarus.MemoryClient` Python SDK. Recall before a root user turn; capture
  only that turn's user text and completed final model response afterward.
- **Local `SKILL.md` files:** procedural knowledge discovered and invoked by the
  imported native skill loader. No second skill engine and no skill upload.
- **Native rollouts:** the authoritative local transcript remains intact. Remote
  memory is retrieved context, not a replacement session journal or verification.

References: <https://syntarus.com/>,
<https://syntarus.com/pages/api-reference>, and the installed public SDK API.

## Configure

Desktop **Settings → Memory** provides a password input for the project key,
an explicit enable/consent control, project/personal scope and sync status.
Keys use the existing Windows DPAPI vault under `SYNTARUS_API_KEY`. Metadata,
responses, native subprocess arguments, tool environments and skills never
receive the key. Saving a key alone does not authorize automatic uploads.

The API base is explicitly `https://ai.syntarus.com/syntarus-api/v1`:
older SDK defaults use another path. Chat, CLI, goals and Telegram-controlled
goals pass through the same provider adapter. There is no raw-HTTP memory
fallback and no dependence on legacy `syntarus_adapter.py`/Python agent loops.

Project mode uses one stable user identity and an agent identity derived from
the canonical project path; Windows verbatim paths normalize identically.
Personal mode explicitly shares a user namespace across projects. Advanced
settings accept an existing Syntarus user ID. Changing a scope/identity does
not import or migrate another namespace. Key rotation never sends old pending
payloads to a different project's key.

Existing local history is **not bulk-imported**. Future opted-in conversations
populate memory; past hosted memories are recalled under the selected identity
and scope. Reading credentials from ambient environment variables does not
bypass Settings consent. Removing a key/disabling stops future sync; it does
not delete hosted data. Use Syntarus's export/deletion controls for hosted data.

`smara memory status` is model-free and reports local configuration, sync counts
and skill paths. **Test memory connection** sends only a fixed synthetic search
query after an explicit notice. It tests reads, not write scope/ingestion quality.

## Lifecycle and failures

Recall is cached once per native root thread/turn, not once per tool step.
Compaction, native memory maintenance, workers, anonymous probes, tool outputs,
reasoning traces and environment-only messages do not populate personal memory.
Recalled text is bounded untrusted evidence; current instructions and live
evidence win. Remote memory never grants permissions or supplies trusted skills.

The root final response is captured only after a valid completed provider stream
without tool calls. Partial/failed streams and observed interrupted turns are
not captured. A response is labelled `completed_response`, **not** a verified
business outcome. Current user/assistant text can include private information;
the enable notice expressly covers this transfer. Known credential forms and
provider keys are redacted; this is not a guarantee to detect arbitrary secrets.

The bounded SQLite outbox uses deterministic idempotency keys and atomic leases
across processes. On Windows queued conversation payloads are DPAPI-encrypted;
successful/terminal-failed payloads are scrubbed. Submission is `submitted`, not
`succeeded`: the SDK's event endpoint must report successful ingestion. Network
errors retry at most three times. Pending event polling is bounded to one day.
Restart can resume the same outbox without repeating accepted submission.
Disable/key/scope changes are rechecked before sending. Pending writes resume
when an enabled native session is running; this is not an always-on memory daemon.

Recall uses up to five hits and a 4,000-character redacted query; context is
bounded to 12,000 characters. Capture exceeding 1 MB is explicitly skipped and
reported, never silently cropped. The queue admits at most 500 pending writes.
These are memory-service bounds, not input/output limits on native chat/history.
Memory failure never means the local conversation was deleted.

## Procedural files

Project workflows: `<project>/.agents/skills/<name>/SKILL.md`.
Personal Smara workflows: `<Smara native runtime home>/skills/<name>/SKILL.md`.
The existing native loader also honors inherited/configured roots; Settings
shows the primary Smara roots, not an authoritative inventory of every skill.

```markdown
---
name: research-review
description: Review a research deliverable for citations, gaps and readability.
---
Read the objective and acceptance criteria. Inspect the evidence and actual
deliverable. Report unsupported conclusions and uncertainty. Never treat
successful generation alone as successful verification.
```

Scripts/references/assets stay alongside a skill. The native loader injects
descriptions and loads the selected procedure when needed. Core-generated
memory maintenance and external memory import are explicitly disabled so they
cannot silently compete with Continuum. Native permissions remain unchanged.

## Verification boundaries

Synthetic SDK/HTTP and native protocol tests establish transport, identity,
idempotency, interruption, retry and UI contracts—not hosted recall quality.
A real Syntarus write → successful event → cross-session recall still requires
the user to add a key and approve a synthetic hosted test. No private history
or paid model traffic is necessary for that test. No claim of “bug-free” or
complete recall is made from offline tests.
