# Smara source-runtime migration

The full public OpenAI Codex source is owned by this checkout in `vendor/codex`,
not downloaded at runtime and not delegated to an installed `codex` command.
`UPSTREAM.json` records the exact imported revision and our modifications.
Retain `vendor/codex/LICENSE`, `NOTICE`, and third-party license notices when
redistributing source or binaries. This is a Smara fork, not an OpenAI product.

The `smara-native` Cargo binary builds the original upstream CLI entry point.
Consequently the model/tool loop, session rollouts, approvals, shell execution,
patch application, sandbox implementations, MCP and agent orchestration come
from the copied Rust implementation, rather than a rewritten Python equivalent.
Upstream names inside the code are retained for attribution and maintainability.

## Build

Run `powershell -File scripts/build-smara-native.ps1` from the repository root.
For a debug acceptance candidate use `-Profile dev`; production packaging always
uses the release profile. `smara source-status` is offline. New `smara` commands
delegate to our own compiled Rust binary. The primary CLI no longer dispatches
`legacy`, and its package does not include the old engine. The Desktop compiles
`src/native_main.rs`, not the archived broad execution bridge, and packages a
thin native transport/settings companion. The Desktop candidate connects directly to
the native JSON-RPC thread/turn protocol, rather than converting it into the old
Python session protocol. Native sessions use a separate `Smara/native-runtime`
home so neither old Smara history nor this machine's Codex account is overwritten.
The CLI runs without a detached shared daemon while the provider adapter is
per-invocation; persistent daemon hosting needs its own adapter-service lifecycle.
Upstream OpenAI installer, self-update and hosted-cloud commands are blocked so
they cannot replace this fork or launch another product.
The build uses the copied Cargo lockfile. The source includes Windows sandbox
helpers; the build must ship those helpers with the main executable. A successful
Cargo check alone does not prove OS-level isolation.

## Migration boundary

The pre-migration implementation is recoverable on local branch
`codex/pre-source-migration-20261008` at `343ee23`. Existing untracked files,
credentials, user documents, conversations, and local databases are not deleted.
Historical Python source/data is retained for recovery and compatibility tests;
it is not selectable or bundled as the primary app's execution engine.

The native project registry and selected model/search provider live in
`Smara/native-settings.json`. Existing `desktop.json`, `desktop-ui.json` and
DPAPI vault files are read for compatibility, not overwritten during bootstrap.
Projects have separate native conversation lists; cross-project resume is
rejected, and project switches reset optional tool/worker/browser choices.
Secrets are saved through private stdin and DPAPI, never metadata or UI storage.
Native search uses provider-bound endpoints and readable missing-key/auth/quota
errors; it does not silently switch a deliberately selected provider.

Only public source is imported. The commercial desktop UI and hosted services
are not supplied by this repository. Smara's Desktop remains its own UI.
Non-Responses providers require a tested transport adapter; do not pretend that
changing a base URL makes Chat Completions compatible with Responses.

The Chat endpoint adapter is authenticated loopback-only, with the real model
credential retained in the transport process. It translates namespaced function
tools and freeform patch tools, preserves message/tool history, streams actual
provider text, and reports truncated/invalid output as failure or incomplete.
Hosted tools that a Chat endpoint cannot support are rejected explicitly. Native
hosted web-search is disabled for this adapter. The opt-in `smara_readers` MCP
server provides public search/fetch/paginated source text, the actual clock, and
workspace-local memory reads. It is not the old research planner. Interactive
DOM browsing now has a source-only opt-in adapter; screenshot computer tools
and Syntarus are not yet migrated. Saved legacy research
schedules are not automatically executed by the candidate.

The development entry points may be registered in the repository virtualenv
after a native build. This does not install or replace the Desktop app. A native
Python wheel is not a standalone native distribution: it contains Python code,
source provenance and license notices, but not the Rust executables. Use the
portable CLI's adjacent `native/` directory or explicitly set `SMARA_NATIVE_BINARY`
to your own build. Never point it at an unrelated installed Codex.

## Use the candidate

`smara --help` shows the upstream native commands plus Smara's integrations.
`smara source-status` works without a configured model. Ordinary coding commands
use the selected model, native workspace sandbox and approval requests.

```powershell
smara --smara-tools exec "Read the current clock, find public sources, and cite fetched evidence."
smara --smara-workers exec "Delegate isolated coding work from committed HEAD; return changes for review, never merge."
smara --smara-browser-origin https://example.org exec "Inspect this approved public site; ask permission for every browser call."
smara schedule --help
smara schedule list
```

Public readers send search queries to the configured search service and fetch
public pages without personal cookies. Memory reads only `.smara/native-memory.md`
inside the selected workspace; it does not scan private files or contact Syntarus.
Readers are off unless selected in Desktop or enabled with `--smara-tools`.

Isolated coding workers are separately off by default. With `--smara-workers`,
the native parent can request `spawn_agent(worktree=true)` in a repository root
under workspace-write permissions. Each worker starts from committed HEAD with
fresh history and a detached checkout under `.smara/worker-worktrees`. Its write
permissions are intersected with the parent's and confined to that checkout;
network access is disabled. Worktree creation/registered cold restore are native,
not a Python scheduler. Changes are retained for review, with no automatic merge
or destructive cleanup. V1 local workers only; remote/V2 worktree support is not
claimed. Worktrees may contain private committed files: normal model-provider
privacy and approval requirements still apply.

The DOM browser accepts up to eight explicitly configured public HTTP(S) origins
via repeated `--smara-browser-origin` flags or the Desktop origin field before
connecting. Its fresh owned browser has no personal cookies, password/file-input
actions, downloads, arbitrary locators or model-supplied JavaScript. Each tool
call requires native confirmation; accepting one action is not a persistent grant.
Observation-bound references and paginated page text avoid stale actions and
silent text clipping. This is DOM text, not screenshot vision; origin guards are
not an OS/network sandbox or a complete DNS-rebinding defense. A supported local
Chromium/Edge/Chrome executable is required; it is not downloaded automatically.
These new integration paths have debug-runtime acceptance, not an installed or
release-package gate. See `NEXT_GATES_2026-10-09.md` for this follow-up.

Native schedules require explicit workspace, profile, prompt file and request
ceiling. `schedule add` creates a **paused** read-only job; `enable` activates it,
and `tick` or the foreground `run` supervisor dispatches it. There is no automatic
OS startup or company-operation job. Unknown/failed runs pause rather than replay.
`completed` means the native turn finished, not that a business outcome was verified.

The v0.1.9 local migration candidate requires release-package and installed-path
acceptance in addition to the bounded native/runtime checks. Broader production
certification still requires human approval-dialog checks, Windows escape and
worker isolation gates, browser integration and maintained real-model quality
evaluations. An explicitly authorized candidate install is not that certification
or a public release. See `VERIFICATION_2026-10-09.md` for the latest results and
limits; `VERIFICATION_2026-10-08.md` is retained as historical evidence.
