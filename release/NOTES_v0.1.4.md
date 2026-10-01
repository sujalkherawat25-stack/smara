# Smara v0.1.4 — Coding Context & Safety Hardening (Beta)

- Preserve coding source context instead of prematurely removing function bodies.
- Use a concise coding prompt and bounded Sarvam GLM reasoning; research settings are unchanged.
- Stop missing-policy/repeated-read loops honestly, without stopping distinct review reads.
- Correct source-read receipts and respect remaining session time during provider retries.
- Prevent Git conflict resolution from modifying paths outside the workspace.
- Align CLI and Desktop versions at 0.1.4.

Verification: 132 targeted tests passed; one Windows symlink test skipped. Frontend build, Cargo check, frozen-backend probes, local CLI installation and installed Desktop visual checks passed.

Known limitation: the latest three real-provider repository tasks produced two independent code passes but zero fully completed runs. Completion-classifier repair failed and changed a protected existing test. These failures are retained and are not presented as successful autonomous runs. See `release/CODING_DISCOVERY_AND_REPOSITORY_2026-10-01.md`.

The attached Windows x64 NSIS installer updates Smara Desktop. Existing local settings and history were retained during the verified installation. Private-code memory recall remains local-only.
