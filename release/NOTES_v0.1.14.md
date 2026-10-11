# Smara v0.1.14 candidate

This is a build candidate, not a published release.

- Bound parallel research work and deduplicate/cache repeated calls; expose
  current worker progress and synthesize partial results on budget exhaustion.
- Prevent stale image/document cache hits when file contents change in place.
- Keep completed/failed worker states from being overwritten by stale UI poll
  responses.
- Package the native document and shared-limit modules in Desktop and CLI
  builds.
- Add project-file attachments beside the composer: select and preview local
  files without eager uploads or loading whole folders into the model prompt.
- Restore saved memory, worker and browser-site choices per project, without
  carrying them into another project. Action approvals remain enforced.
- Make clock and explicitly approved image/OCR analysis standard tools, rather
  than hiding them behind the project-memory checkbox.
- Retire the old installer path that registered the legacy Python executor.
- Archive old untracked reports with a hash-verified recovery manifest; keep
  generated outputs out of Git and include document/limit tests in native CI.
- Record the end-to-end Codex source/runtime comparison and the remaining
  Desktop, computer-use, Windows isolation, and model-quality gates in
  `native/SMARA_CODEX_COMPARISON_2026-10-11.md`.

Verification evidence and boundaries are in that comparison note. Installation
and public release are separate approval gates.
