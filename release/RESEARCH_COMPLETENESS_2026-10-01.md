# Question-completeness gate — 2026-10-01

Research cannot complete solely because some passage supports some claim. At the final completion boundary, the agent now requests a separate, tool-free semantic review of the original request and the delivered answer/report. This also covers synthesized answers and provider-timeout recovery paths.

The review has a 2,048-output-token bound and uses the normal provider budget admission and token accounting. Questions above 12,000 characters or answers/reports above 64,000 characters fail closed rather than being silently truncated. Invalid JSON, empty checklists, truncated responses, provider failures, non-boolean decisions, and fabricated answer quotations cannot pass. Missing fields preserve the partial answer but produce an incomplete durable result (`needs_input`) with outstanding requirements.

The receipt records question/answer hashes and the report artifact identity. The durable finish boundary rejects stale receipts. CLI text/JSON and both Desktop research surfaces expose the checklist independently of passage support. Older saved results explicitly say that completeness was not reviewed.

## Verification transparency

New tests cover multi-part answers, missing identifiers/titles, historical cutoffs, one-sided source comparisons, invented quotations, malformed responses, input bounds, provider-error redaction, stale receipts and the actual agent final boundary. Complete Python regression run: 1,019 passed with two offline JWT-fixture warnings. Frontend typecheck/build passed. Additional focused checks verify the last question/report receipt-hash safeguards. Installed-artifact verification is recorded in the task handoff.

Existing offline scripted-provider transports needed an additional JSON review response. W1/W2/W5 fixtures now explicitly provide it; their existing factual/source assertions were retained. W1 fixture provenance and the inventory hashes were updated to document that protocol change. This is a unit-fixture migration, not a real-provider pass. Timeout fixtures now require an incomplete result when review is unavailable. No live-evaluation questions, reference answers, scoring criteria or recorded baseline outcomes were changed.

## Real-provider diagnostic

`release/evidence/LIVE_WEB_COMPLETENESS_2026-10-01.json` contains two unchanged live-evaluation tasks with real Sarvam GLM 5.3/search:

- Historical-as-of task: preserved a partial answer but did NOT claim completion. It lacked the requested version and date. The evaluation still fails; blocking false success is not equivalent to answering correctly.
- RFC task: included a number and title, passed completeness, but supplied an obsolete RFC and FAILED factual scoring. Completeness is not factual freshness.
- Overall: 0/2 passes, one false completion, zero measured safety violations. This subset does not replace the 15/24 full-suite scorecard.
- Conservative token estimate approximately INR 21.77. Combined with prior full-suite and failed-page diagnostic estimates, approximately INR 345.93, below the user-approved INR 450 ceiling. This is not a provider invoice.

## Remaining limitations and next step

The same configured model performs the independent-context review; it may omit a requirement or misunderstand a nuanced question. A quoted sentence is necessary, not sufficient proof of correctness. The added call increases latency and can cause an otherwise supported answer to remain incomplete when provider budget is depleted.

Next: historical cutoff/freshness validation and conflicting-source reconciliation, followed by bounded failed-page recovery. Then rerun the unchanged full suite with multiple repetitions. Current results do not justify a qualified release tag.
