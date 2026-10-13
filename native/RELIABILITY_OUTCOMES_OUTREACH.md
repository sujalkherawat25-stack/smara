# Reliability, outcome checks and reviewed outreach

This batch builds on the existing native engine. It does not add a second
planner, unattended mail campaign engine, personal-browser access or computer use.

## Execution contract

- Known model output allowances survive chat → goals → scheduled execution.
  An explicit goal/schedule `max_output_tokens` may lower the allowance; there
  is no hidden goal-only 4096 or schedule-only 2048 cap.
- Unknown model metadata remains unknown; budgets use a documented conservative
  reservation fallback rather than inventing a provider capability.
- Requests share a local cross-process provider/account admission ledger: two
  concurrent streams, 60 admissions per rolling minute and a bounded 30-second
  queue. Hashes and renewable leases are stored, not API keys or prompt text.
- Goals retain cumulative request/token-estimate limits. These UTF-8 byte based
  estimates are not actual token usage, pricing, or a billing guarantee.
- Goal research time allowances match the cycle. Tool/repeat limits remain;
  the task receives their values and partial synthesis remains bounded.
- Work turns keep normal native tools. Structured output is used only for one
  final checkpoint-format repair, with the tool mapping removed. Strict format
  at the start prevented real work in the first live probe and was removed.
- A formatting repair is charged to the same goal budget. Interrupted cycles
  retain observed native steps and partial outcome evidence without another
  provider request; they do not become completed goals.
- Unexpected worker exceptions block only their goal. Uncertain interrupted
  actions are not automatically retried. Resume preserves the native session.

## Outcome checks

Goals may specify `requirements: [{id, description}]`. Checkpoint `coverage`
links each requirement to claim indexes, output paths and passing native test
commands. Only observed matching quotations, hashed current outputs or actual
post-edit passing commands count as evidence.

The Desktop chat scorecard shows citation-excerpt support, requirement-evidence
coverage, source failures, test failure excerpts and latency. Missing measurements
remain “Not measured.” These are mechanical evidence checks, not semantic
entailment, a guarantee of answer completeness, or visual quality certification.

`scripts/run_native_outcome_suite.py --out NEW_DIRECTORY` previews its catalog
without inference and never claims a quality pass. `--live` requires explicit
approval and uses synthetic projects/public Python docs only. Reports are unique;
cumulative reservations carry forward across authorized continuations. Real
provider failures and incomplete tasks remain visible.

## Research → local draft → reviewed email

Settings → Email opts into LOCAL research-backed drafts and configures Gmail.
The agent gains an approval-gated `outreach_draft` tool, never an email send tool.

Each draft needs company/domain, one explicit address, subject, body and exact
quotes from pages fetched by the same reader session. The address must appear
in a contact excerpt on the company's own site. This prevents guessed contacts;
it does not certify that every personalization statement is supported. Review
the displayed claims, quotes, sources and full message before sending.

- Local draft content is protected with Windows DPAPI. No plaintext token files
  are introduced. Drafts are separated by canonical project identity.
- Same-recipient duplicates are blocked within a project. “Do not contact” keeps
  a suppression record and blocks another draft/send to that recipient.
  Exact repeated draft requests return the existing receipt. User edits to an
  unattempted subject/body change its fingerprint and invalidate old review.
- Gmail uses the documented desktop loopback OAuth flow with state and PKCE.
  Users complete consent in the system browser. Their own Google Desktop OAuth
  client and Gmail API configuration are required. Tokens are stored in the
  protected vault and refreshed without exposing them to the model/UI.
- Requested scopes are `gmail.send` and `gmail.readonly`, for sending plus exact
  Sent-record reconciliation. Google verification/consent restrictions are not
  bypassed. Local drafts do not require an account.
- Sending requires an exact recipient cohort, a rolling daily attempt allowance
  (default five; explicit maximum twenty), and review bound to immutable content.
  Model tools cannot send, edit the cohort, or remove review requirements.
- Attempts are committed before dispatch. Accepted responses retain provider
  message/thread IDs. Crashes/timeouts/ambiguous responses are UNKNOWN and are
  never resent automatically. Reconciliation uses the stable MIME Message-ID
  and verifies Sent label/sender/recipient; it does not establish delivery/reply.
- No automatic follow-ups are enabled. No real email is sent by tests.

CLI `smara mail list`, `connect`, and `reconcile --id ID` expose local management;
actual sending stays in the Desktop review UI. `connect` opens Google consent
only on the user's explicit action. Old Python Gmail execution is not reused.
Disconnect removes the local token and invalidates pending authorization;
revoking Google account access is a separate user action. The review list is
bounded to the latest 20 records; older receipts can be reconciled by CLI ID.

## Release gates

Source/component/native protocol tests are not installed-GUI certification.
Before publishing/installing a new release, retain the actual live outcome
reports, run a scoped installed-GUI check, and complete real Gmail consent plus
a separately approved self-addressed send/read-back test. Hosted Syntarus
write → ingestion → cross-session recall still needs its own approval/key.

## Provider documentation

- [Google desktop OAuth, PKCE and loopback redirect](https://developers.google.com/identity/protocols/oauth2/native-app)
- [Gmail send response](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/send)
- [Gmail query and scopes](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/list)
