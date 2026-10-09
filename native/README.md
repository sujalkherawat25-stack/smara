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
delegate to our own compiled Rust binary; `smara legacy ...` is explicit legacy
access, never an automatic fallback. The Desktop candidate connects directly to
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
Legacy Python code is retained until native replacement acceptance passes.

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
browser/computer tools and Syntarus are not yet migrated. Saved legacy research
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
smara schedule --help
smara schedule list
```

Public readers send search queries to the configured search service and fetch
public pages without personal cookies. Memory reads only `.smara/native-memory.md`
inside the selected workspace; it does not scan private files or contact Syntarus.
Readers are off unless selected in Desktop or enabled with `--smara-tools`.

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
