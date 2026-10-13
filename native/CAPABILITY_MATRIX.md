# Current capability matrix — v0.1.23 candidate

This is the current shipping-scope reference. Older gate files are historical,
not proof that this candidate passed. Model output, native turn completion and
business outcome verification are different things.

| Capability | Current status | Boundary / remaining gate |
| --- | --- | --- |
| Native coding, commands, patches and saved sessions | Implemented; native recovery and synthetic coding verified | Real-model coding tests passed unchanged; reviewed evidence is still required |
| Parallel committed-snapshot coding workers | Actual native isolation/cold-restart checks passed | Parent unchanged; no automatic merge; not unrestricted workers |
| Autonomous `/goal`, shared background worker, Telegram controls | Implemented and regression-tested | Bounded cycles/requests/permissions; laptop awake/online; no unattended blanket grants |
| Web search and public page readers | Implemented; live public reads observed | Failed pages are errors, not fabricated sources; latest questions need current clock evidence |
| Cold research pagination | Project-scoped snapshots now survive restart | Public text only, 24-hour retention and bounded cache; retrieval timestamp retained, not a fresh fetch |
| Outcome/evidence scorecards | Implemented; fail closed on gaps | Excerpt presence and evidence linkage do not prove semantic correctness |
| Chat progress, waiting reasons, worker Stop and recovery | Frontend tests and actual native recovery passed | Installed candidate GUI still requires fresh verification |
| File previews, Git diffs, stage/revert/Undo | Implemented, project-bound and tested | Recovery receipts and activity locks; no executable preview |
| Syntarus Continuum + file skills | SDK integration and local queue tests passed | Remote writes require opt-in; live hosted-memory quality not tested in this gate |
| Sarvam OCR/image/voice | Separate approval-gated adapters | This gate did not rerun paid speech/OCR; not general visual computer control |
| Public interactive browser | Controlled owned browser and origin-bound tools | No personal cookies; login and file transfer are separate opt-ins; not OS/network certification |
| Windows selected-app computer actions | Experimental, off unless explicitly selected | Human permissions/consent; not unrestricted desktop automation; broad live acceptance incomplete |
| Gmail researched drafts/reviewed sending | Implemented and synthetic fault-tested | Real account OAuth and self-addressed send remain unverified; no automatic campaign sending |
| Docker fixture isolation | Actual engine tests passed | Separate from native Windows sandbox; not a claim that every action runs in Docker |
| Windows interactive terminal | **Disabled** | Actual no-network loopback probe failed; readiness/MXC availability is not confinement proof |
| Windows agent command network boundary | **Not certified** | File write boundaries passed; the no-network claim must not be generalized |

Release blockers: remaining live research quality, fresh installed-GUI checks and
human Deny verification. Never publish this as fully production-ready merely
because deterministic tests pass. The terminal is deliberately disabled until
its exact execution path passes its confinement gate.

## Exact-source/build discipline

`scripts/native_release_manifest.py capture` refuses dirty or untracked build
inputs while preserving unrelated user report deletions. `verify` rejects a
different commit/input fingerprint; `seal` records the tested artifact hashes.
It does not imply model quality, human approvals, or installed-GUI acceptance.
Any source change after sealing requires another build and relevant verification.
