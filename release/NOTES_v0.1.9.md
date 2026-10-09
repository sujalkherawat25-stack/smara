# Smara v0.1.9 — source-native migration candidate

Smara now owns the imported public Rust harness source, rather than invoking a
separately installed Codex or running a second Python planning loop. The selected
Sarvam/compatible Chat provider is connected through an authenticated loopback
wire adapter. Sessions, execution, approvals, MCP, workers and recovery belong
to the native harness. Upstream attribution and Apache-2.0 notices are retained.

Desktop opens the native interface by default. Legacy settings and old history
remain separately accessible. Native history is stored separately; old chats
are not automatically converted. Public research/clock/local-memory readers are
opt-in. Scheduled native tasks are paused by default and have bounded dispatch;
no background company-operation jobs are enabled by this update.

This is a development migration candidate, not a claim of full product parity.
Parent-directed isolated coding workers, interactive browser/computer use,
remote-memory integration and broad long-running/provider-quality gates remain.
Security decisions are not auto-approved. Human approval-dialog checks remain
separate from the automated native approval-protocol tests.

See `native/VERIFICATION_2026-10-09.md` for measured results, failed original runs,
installation status and explicit limits. No existing history or credentials
should be deleted during installation. Native `--version` retains the upstream
development build version; Desktop/package version is v0.1.9.
