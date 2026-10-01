# Coding, memory and Desktop hardening — 2026-10-01

This change deliberately leaves the research pipeline alone.

- Syntarus access uses the documented SDK base `https://ai.syntarus.com/syntarus-api/v1`, the configured project key, a stable user ID, and a distinct workspace agent scope. Smara login tokens and the shared community credential are no longer used as memory credentials.
- Memory search health is measured through authenticated search, not inferred from a configured key or a public health response. Local notes are no longer presented as cloud recall.
- Explicit memory writes preserve asynchronous event IDs and idempotency keys. Accepted writes are shown as queued, not as verified ingestion/recall. Bootstrap marketing notes and generated ADRs are not uploaded; new workspaces start without invented ADRs.
- Coding turns retrieve bounded saved local notes without indexing the whole repository. The user explicitly selected local-only automatic recall. Coding prompts are not uploaded automatically. Cloud sync/search are explicit memory actions.
- Added `smara memory configure <stable-non-secret-user-id>` to save workspace memory identity. A Syntarus key is present locally, but no stable user identity was configured during this work. Real cloud ingestion/recall therefore remains unverified and requires identity setup.
- Terminal polling no longer advances over unread long output. UTF-8 boundaries are preserved, remaining output is visible, and polling can drain logs after process exit.
- Desktop composer participates in layout instead of overlapping the feed. Transcript scrolling can shrink correctly. Memory status and upload notices no longer claim an unmeasured LoCoMo score or verified cloud sync.

## Verification

The broad regression run passed 1,023 tests, skipped one browser-dependent check, and emitted two offline JWT-fixture warnings. Subsequent focused tests passed 21 checks, including long ASCII/Unicode logs, SDK payload scope, event receipts, empty initial memory and the explicit local-only recall constraint. CLI help exposes identity setup. Frontend typecheck/build passed. Final packaging and installed checks are recorded in the handoff; build success is not native visual UX verification.

SDK response fixtures in unit tests are not a live Syntarus round-trip claim. No API keys or memory contents are printed in health messages.

Next: complete identity setup and a deliberate write → processing-event → recall check; then exercise one real coding task with visible diff, test command, exit code, and cancellation/restart behavior. Desktop still needs interactive UX verification and the user's concrete problem report; these scoped fixes do not constitute a full redesign.

API contract source: https://syntarus.com/pages/api-reference
