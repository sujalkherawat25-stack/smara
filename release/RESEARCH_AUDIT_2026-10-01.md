# Research reliability audit — 2026-10-01

## Delivered in this follow-up

- Sarvam GLM 5.3 receives explicit low reasoning effort for quick research and high effort for other research modes. Other providers are unchanged. Sarvam documents that omitted effort defaults to max: https://docs.sarvam.ai/api/getting-started/models/openweight/glm-5-3.
- Provider budget exceptions stop immediately instead of being retried, and are reported as budget exhaustion.
- Research tool guidance parses JSON booleans rather than matching the word `passed`; unsupported resolutions no longer tell the agent to conclude successfully.
- Final-claim reconciliation includes blocked nodes and does not assign a single final paragraph across a multi-question graph.
- Malformed or empty provider completions fail closed rather than crashing with IndexError.
- Desktop selects the newest full 24-task scorecard, distinguishes stopped runs, and bundles full-suite reports. Diagnostic subsets cannot replace the full scorecard.
- Evaluation resume requires unchanged time, iteration, and token ceilings. A higher monetary ceiling requires an explicit extension flag and records the change in the report.

These extend the previously delivered shared CLI/Desktop research workflow, claim-to-source review, persistent research watches, and native executor/packaging fixes. They do not establish industry parity or eliminate all false completions.

## Real-provider evidence

`release/evidence/LIVE_WEB_ACCEPTANCE_V5_2026-10-01.json` contains all 24 tasks, one repetition, real Sarvam GLM 5.3 and live search. The original 15 attempts were retained when the user approved raising the conservative total ceiling from INR 250 to INR 450.

- Passed: 15/24 (62.5%); gate FAILED.
- False completions: 8.
- Answer coverage and citation support: 79.86% each, as measured by the suite.
- Latency: mean 21.34 seconds, median 17.00 seconds, p95 44.44 seconds.
- Measured safety violations: 0; one crashed attempt had unmeasured safety.
- Historical-as-of, source-conflict, and failed-page categories: zero passes.
- Full-suite conservative token estimate: approximately INR 300.10; this is NOT an actual provider invoice.

The full report predates the empty-completion guard. The separate failed-page recheck in `LIVE_WEB_ACCEPTANCE_V5_FAILED_PAGE_2026-10-01.json` did not crash or falsely complete, but still FAILED: it exhausted its 12-iteration limit without a complete tool chain. It used 60,757 billed tokens, adding approximately INR 24.06 under the same conservative estimator. Combined estimated spend remains below the approved INR 450 ceiling. The recheck is not merged into the full scorecard.

No benchmark answers, sealed reference hashes, or acceptance criteria were changed to turn failures into passes. Offline unit fixtures are distinct from the real-provider evaluation.

## Verification and deployment record

Final Python regression run: 997 passed, with two warnings about the short JWT key in an offline test fixture. Five newly added budget-resume rejection tests also passed separately. Native tests: 18 passed. Frontend typecheck/build and the checkout-independent frozen executor probe passed. Installed-binary checks and remote sync outcomes are recorded in the task handoff; they must not be inferred solely from this source audit.

## Next work, in order

1. Add a bounded question-completeness review before marking research completed. A supported quotation does not prove that every requested field, date cutoff, or comparison has been answered. Preserve explicit incomplete states when review fails.
2. Improve historical cutoff handling, conflicting-source reconciliation, and bounded failed-page recovery. Avoid spending the entire iteration budget searching for an unavailable page.
3. Rerun the unchanged full suite, with maintained multi-repeat qualification, before creating a qualified release tag. Current results do not justify one.
4. Perform interactive installed-app checks for evidence restoration, cancellation, watch refresh, and scorecard rendering. Build and process probes alone are not visual UX verification.
