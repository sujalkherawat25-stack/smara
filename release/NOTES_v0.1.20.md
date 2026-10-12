# Smara v0.1.20

This release hardens the native Codex-compatible runtime and its Sarvam model
adapter, and improves autonomous-goal recovery diagnostics.

- Give Sarvam Chat Completions a bounded, explicit output-token budget instead
  of inheriting the provider's short 2,048-token default; map Codex reasoning
  levels and structured output to the documented Sarvam fields.
- Validate Sarvam's exact API version, endpoint path, and supported model IDs;
  migrate earlier DeepSeek aliases to the current `deepseekv4.1-flash` ID while
  preserving the V1-only Conversations model on `/v1`.
- Keep private reasoning traces out of visible assistant text, report provider
  content filtering distinctly, and show actionable provider/setup errors.
- Preserve incomplete-output state so users can continue without replaying
  completed actions; make goal-cycle failures identify a safe stage/context
  instead of exposing raw provider or filesystem exception details.

Verification: full Python suite, focused provider/profile regressions, Desktop
tests, TypeScript/Vite production build, and native Rust tests. No live-model
quality claim is made by these offline checks.
