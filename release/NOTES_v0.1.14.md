# Smara v0.1.14 candidate

This is a build candidate, not a published release.

- Bound parallel research work and deduplicate/cache repeated calls; expose
  current worker progress and synthesize partial results on budget exhaustion.
- Prevent stale image/document cache hits when file contents change in place.
- Keep completed/failed worker states from being overwritten by stale UI poll
  responses.
- Package the native document and shared-limit modules in Desktop and CLI
  builds.
- Record the end-to-end Codex source/runtime comparison and the remaining
  Desktop, computer-use, Windows isolation, and model-quality gates in
  `native/SMARA_CODEX_COMPARISON_2026-10-11.md`.

Verification evidence and boundaries are in that comparison note. Installation
and public release are separate approval gates.
