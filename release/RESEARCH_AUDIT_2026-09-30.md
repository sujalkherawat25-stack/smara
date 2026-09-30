# Research reliability audit — 2026-09-30

This is a development update, not a research-qualified release.

## Implemented

- Canonical research workflow shared by CLI, Research workspace, and research
  chat. Chat preserves the selected Desktop provider, conversation context,
  durable evidence review, and cancellation checks.
- Claim-to-source review in chat with fetched passages, timestamps, failed
  pages, and a daily research-refresh action. Latest receipts are restored
  from the durable runtime journal on restart.
- Research-watch SQLite connections close deterministically. Refreshes have
  a one-hour minimum interval and retain the last successful baseline even
  after more than twenty failed refresh attempts.
- Native bridge stdout/stderr are drained while the child runs. A bridge
  failure does not replay a potentially mutating operation under another
  interpreter.
- Frozen package includes the dynamic canonical/watch/chat modules and the
  recorded 24-task scorecard.
- Retrieval candidates cannot resolve questions automatically. Removed
  hardcoded canonical answer strings, Cargo source-floor padding, and
  evaluation-scaffold-specific lane routing. Sealed evaluation references
  remain validator-only.

## Verification

- Full local Python suite: 984 passed, two short test-fixture JWT-key warnings.
- Native Rust bridge suite: 17 passed.
- Final routing/privacy/research contract checks: 24 passed after removing
  evaluation-specific routing. The full-suite count above predates only that
  routing change; its focused regressions were rerun.
- Frontend typecheck and production build passed.
- Actual frozen executor probe passed from a temporary directory with no
  checkout import override and no Python on PATH. This is an offline storage
  and packaging check, not a substitute for real-model evaluation.

## Real-provider results

All runs used configured Sarvam GLM 5.3 and Exa, not model mocks. Reports are
kept separately; subsets were not merged into an inflated full-suite score.

| Report | Passed / attempted | False completions | Gate |
| --- | --- | --- | --- |
| LIVE_WEB_ACCEPTANCE_V5.json, earlier baseline | 14 / 24 | 5 | Failed |
| LIVE_WEB_ACCEPTANCE_V5_RECHECK.json, ten failed-task diagnostics | 2 / 10 | 3 | Failed |
| LIVE_WEB_ACCEPTANCE_V5_COMPLETION_FIX.json, after disabling auto-resolution | 0 / 1 | 0 | Failed, cost ceiling |

The last diagnostic requested two tasks but attempted only one before its
conservative spend-reservation ceiling. It found the correct PEP source, did
not falsely complete, and still exhausted its token budget. It predates the
final unresolved-node recovery guidance and topic-neutral routing changes.
There is no green real-model result for the final build.

## Next gate

1. Fix the resolve/validate retry loop and bound model reasoning cost without
   weakening evidence checks. Budget exhaustion currently surfaces as a tool
   error in some paths and should have its own truthful status.
2. Add semantic question/answer relevance checks alongside lexical passage
   support. Quote support alone is not an answer-completeness guarantee.
3. Rerun all 24 real tasks on the final implementation, with repetitions for
   release qualification; publish failures, coverage, support, latency, and cost.
4. Visually exercise installed chat, receipt restoration, refresh scheduling,
   cancellation, and long-answer scrolling. Build checks alone do not establish
   zero UI bugs.

Do not create a new research-qualified release/tag or advertise head-to-head
industry parity from these results.
