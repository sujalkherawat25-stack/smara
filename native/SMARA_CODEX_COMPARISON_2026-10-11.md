# Smara vs. Codex: end-to-end review (2026-10-11)

## Bottom line

Smara is not merely imitating a Codex-style loop: its native CLI and Desktop
transport use the public Codex Rust execution source imported at
`14c8b7771ab2b617a131f5d8e55e98d18e56ed09` (see `UPSTREAM.json`). This brings
the core agent harness into Smara. It does **not** bring the proprietary Codex
desktop product, its complete UI, or every hosted capability. Smara adds its
own provider, settings, research, and application layers around that core.

OpenAI's architecture describes the harness as owning the model/tool loop,
session state, routing, approvals, tracing, and recovery, while the execution
environment supplies isolation. Codex's app-server is the integration boundary
for a product-owned UI and persistent, streamed conversations. Smara's native
runtime is built on this same public-source direction, but its own product/UI
and integrations remain a separate implementation.

References: [Agents API architecture](https://developers.openai.com/api/docs/guides/agents-api/architecture),
[sandbox agents](https://developers.openai.com/api/docs/guides/agents/sandboxes),
[Codex as a platform](https://developers.openai.com/codex/guides/agents-sdk/).

## What is present

- The upstream model/tool turn loop, streaming events, conversation rollouts,
  approvals, patching, shell routing, MCP, delegation, and sandbox adapters are
  compiled into Smara's `smara-native` runtime.
- Smara's Desktop speaks the native thread/turn protocol through its own Tauri
  transport. Its history/settings stay in a separate Smara runtime home; it
  does not use an installed Codex binary or Codex account.
- Smara has a provider adapter for supported chat-completions endpoints,
  provider/search settings, native research readers, optional worker/browser
  tools, and Sarvam document/image support. Adapter limitations are explicit;
  changing a URL alone does not make an incompatible API supported.
- Research has bounded per-turn/root-worker tool and inference budgets,
  duplicate-call controls, shared caching, progress updates, and a final
  synthesis attempt when a shared limit is reached.
- Document/image analysis is approval-gated and uses a content fingerprint for
  caching, so replacing a file at the same path does not return stale results.
- Worker status rendering now prevents an old polling snapshot from overwriting
  a newer terminal event such as `completed` or `failed`.

## Verified in this candidate

- Full Python run: 1,414 passed, 26 skipped; one Docker test initially could
  not start because the CI-declared images were absent. After preparing the
  same images as CI, the complete Docker lane (worker and sandbox tests) passed
  21/21. The entire Python suite was not rerun after image preparation.
- Focused native provider/tools/search/document/limits tests: 88 passed, 1
  skipped.
- Desktop frontend: 58 tests passed; Vite production build passed.
- Rust `cargo check --locked`: passed.
- Offline native protocol acceptance: passed handshake, thread/turn streaming,
  real patch, approval denial, denied-command non-execution, resume/list, and
  shutdown checks; four scripted provider requests, no paid inference.
- Native terminal acceptance: passed buffered output, nonzero exit, workspace
  confinement, blocked external network, PTY streaming, Unicode stdin, resize,
  and terminate checks. This runner did not exercise the Desktop GUI and is not
  a certification of every Windows OS-sandbox escape case.
- No live Sarvam/model request was made during this audit/build.

## Important differences and remaining work

1. **Computer use:** Smara does not have general screenshot-driven computer
   control or arbitrary interaction with the user's desktop. The optional
   browser is a public-origin DOM reader; it is not the user's personal
   browser, and it does not imply OS-level computer use.
2. **Desktop product parity:** Smara has its own Tauri project/chat/settings
   UI, not Codex's commercial desktop UI and all its project organization,
   review, history, and interaction polish. Treat screenshots and a manual
   check of this exact installer as a separate acceptance gate.
3. **Windows isolation/interactive terminal:** automated terminal acceptance
   passes, but it did not exercise the GUI and does not establish a broad
   Windows escape guarantee. Keep execution fail-closed if Docker/native
   sandbox prerequisites are missing; do not fall back to unsandboxed host
   execution.
4. **Provider parity:** only explicitly supported provider protocols/features
   work. Hosted Codex-only features are not automatically available to Sarvam
   or another chat-completions model.
5. **Outcome quality and autonomy:** source-level harness parity is not proof
   of equal research/coding success rates. Model-specific evals, user-visible
   approval/recovery checks, and scheduled company workflows still need
   dedicated outcome-level validation.

## Recommended next gate

Run the exact packaged v0.1.14 candidate through manual Desktop GUI checks for
connect, tool approval/deny, stop, disconnect/reconnect, worker completion,
large output, and terminal output. Then run the Windows sandbox escape suite on
the supported Windows/Docker configuration. Only after those pass should this
candidate be installed or published as a release.
