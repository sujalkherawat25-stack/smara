# Current capability matrix — v0.1.23 released

This is the current shipping-scope reference. Older gate files are historical,
not proof of current acceptance. Model output, native turn completion and
business outcome verification are different things.

Released source: `4afbe276d031e7290cabf916e8a17b0edf14d848`. This post-release
documentation update does not change the tested source, tag or installer.

| Capability | Current status | Boundary / remaining gate |
| --- | --- | --- |
| Native coding, commands, patches and saved sessions | Implemented; native recovery and synthetic coding verified | Real-model coding tests passed unchanged; reviewed evidence is still required |
| Parallel committed-snapshot coding workers | Actual native isolation/cold-restart checks passed | Parent unchanged; no automatic merge; not unrestricted workers |
| Autonomous `/goal`, shared background worker, Telegram controls | Implemented and regression-tested | Bounded cycles/requests/permissions; laptop awake/online; no unattended blanket grants |
| Web search and public page readers | Implemented; live public reads observed | Failed pages are errors, not fabricated sources; latest questions need current clock evidence |
| Cold research pagination | Project-scoped snapshots now survive restart | Public text only, 24-hour retention and bounded cache; retrieval timestamp retained, not a fresh fetch |
| Outcome/evidence scorecards | Implemented; fail closed on gaps | Excerpt presence and evidence linkage do not prove semantic correctness |
| Chat progress, waiting reasons, worker Stop and recovery | Frontend/native recovery passed; installed GUI inspected | Send/Stop/reconnect cancellation report passed; final human Deny confirmed by GUI and durable session |
| File previews, Git diffs, stage/revert/Undo | Implemented, project-bound and tested | Recovery receipts and activity locks; no executable preview |
| Syntarus Continuum + file skills | SDK integration and local queue tests passed | Remote writes require opt-in; live hosted-memory quality not tested in this gate |
| Sarvam OCR/image/voice | Separate approval-gated adapters | This gate did not rerun paid speech/OCR; not general visual computer control |
| Public interactive browser | Controlled owned browser and origin-bound tools | No personal cookies; login and file transfer are separate opt-ins; not OS/network certification |
| Windows selected-app computer actions | Experimental, off unless explicitly selected | Human permissions/consent; not unrestricted desktop automation; broad live acceptance incomplete |
| Gmail researched drafts/reviewed sending | Implemented and synthetic fault-tested | Real account OAuth and self-addressed send remain unverified; no automatic campaign sending |
| Docker fixture isolation | Actual engine tests passed | Separate from native Windows sandbox; not a claim that every action runs in Docker |
| Windows interactive terminal | MXC execution gate passed; fixed installed terminal retest passed by human report | 17 real probes, including exact Desktop shell argv, two submitted commands, personal-history disabled, PTY/Unicode/resize/Stop, TCP/UDP/IPv6, descendants and junction writes; no automatic weaker fallback |
| Windows agent command network boundary | Bounded MXC probes passed; **not universal certification** | Earlier restricted-token loopback failure retained; unsupported MXC hosts fail closed |

Both fresh public-documentation research cases reached ready-for-review within
eight real requests. This is evidence linkage, not semantic/business certification.
Post-fix native patches and workers passed on the same core as the terminal gate.
Release gates completed: sealed package/installed payload checks, nine installed
CLI checks, 13 installed synthetic settings checks, human terminal retest and
five native Deny checks. All seven CI lanes and secret-audit workflows passed
on both source branches. The exact tested installer is published in
[v0.1.23](https://github.com/sujalkherawat25-stack/smara/releases/tag/v0.1.23).
See [follow-up release gate](USABILITY_RELEASE_GATE_2026-10-14.md). Do not describe
this as unrestricted or fully production-certified merely because tests pass.

## Exact-source/build discipline

`scripts/native_release_manifest.py capture` refuses dirty or untracked build
inputs while preserving unrelated user report deletions. `verify` rejects a
different commit/input fingerprint; `seal` records the tested artifact hashes.
It does not imply model quality, human approvals, or installed-GUI acceptance.
Changes intended for a new sealed package require another build and relevant
verification. Documentation-only follow-ups may advance the branch without
moving the released tag; the installer above remains pinned to its tested commit.
