# Smara: is the architecture goal achieved?

Historical audit dated 2026-10-08. Subsequent reader, scheduling, coding-worktree,
recovery and actual Desktop checks are recorded in
[`VERIFICATION_2026-10-09.md`](VERIFICATION_2026-10-09.md). Limits below describe
the earlier snapshot; they have not been silently rewritten into later passes.

## Plain-language conclusion

The central goal is achieved in the source/development runtime: the model drives
the copied Codex execution engine, and that engine owns tools, permissions,
sandboxing and saved sessions. This is not merely a renamed old Smara loop, nor
a dependency on a separately installed Codex CLI.

The whole-product migration is **not** finished. The installed Desktop is still
the previous app, and research/browser/computer-use/memory tools have not all
been ported to native MCP. A copied implementation gives us working foundations;
it does not prove every model, feature and deployment combination works.

## What actually runs

User message → Smara native CLI or Desktop RPC → copied Rust turn loop →
configured model through a wire adapter → model chooses a tool → native policy
and sandbox/approval checks → actual execution → result added to native history
→ model decides the next action → repeat until completion, interruption or error.

The critical code is `vendor/codex/codex-rs/core/src/session/turn.rs`. It continues
sampling after tool results or pending input and uses the copied context/
compaction and lifecycle machinery. Native tool approval/sandbox orchestration
is in `core/src/tools/sandboxing.rs`. Child configuration inherits the captured
parent approval/permission profile in `core/src/agent/child_config.rs`.

`src/smara/native_provider.py` translates Responses messages/tool schemas to
Chat Completions and returns real provider output. It does not choose tools,
execute patches, create a replacement plan, or approve actions. The Python
bootstrap owns transport/process setup, not agent behavior.

This boundary follows the official architecture: an application owns its UI,
context and integrations; app-server exposes threads, turns, events and approval
requests around the harness. See [OpenAI's harness/platform explanation](https://developers.openai.com/blog/codex-as-a-platform)
and [the app-server protocol](https://learn.chatgpt.com/docs/app-server).

## Status by requirement

| Requirement | Actual status |
| --- | --- |
| Model in the driving seat | Demonstrated with real Sarvam choosing inspection, tests, a patch and verification |
| Reason/action/observation loop | Actual copied Rust loop; tool results returned to the model across six coding requests |
| Human permissions | Native approval requests retained; an actual denied command did not execute in the protocol acceptance |
| OS sandbox | Native implementation built; one Windows workspace/outside write boundary passed; wider escape tests remain |
| Sessions and recovery | Native saved history/resume works across a clean process restart without repeating inference; crash/reboot tests remain |
| Multi-agent execution | Two actual Sarvam-driven read-only workers ran, returned results and closed; separate-worktree coding remains |
| Other model support | Adapter uses configured profiles; Sarvam tested; other providers and context/image capabilities require acceptance |
| Research, browser, computer use, memory | Existing implementations retained, but not yet fully connected to the native authority |
| Installed local Desktop | Not replaced; source candidate builds, visual/installer acceptance remains |

## What no longer controls the native path

- The old Python autonomous loop and rigid goal/DAG execution are not invoked
  for native CLI/Desktop turns.
- Old task routing, Python session-ID translation and argument self-healing do
  not sit between native model choices and the native tool executor.
- No automatic old-agent or installed-Codex fallback is used.
- Invalid or ambiguous selected model profiles fail rather than silently
  switching to the first profile.
- Existing scheduled research jobs are saved but do not automatically start a
  second legacy runtime from this migration candidate.

The old code was retained as explicit recovery/legacy access, not deleted with
personal data. It should be retired after equivalent native adapters pass. The
native sandbox, permission boundaries and model context limits should stay;
those are necessary controls, not obstacles to remove for apparent capability.

## Work completed in this follow-up

1. Audited actual turn-loop, safety, child inheritance and client dispatch paths.
2. Added bounded real-model coding acceptance using only disposable synthetic
   projects. The model reproduced the failure and fixed it; five original tests
   and independent extra cases passed without modifying test files.
3. Ran the task through the native CLI and Desktop's app-server protocol.
4. Verified the real thread's items survived a new native process, without
   starting another inference turn or replaying tool actions.
5. Verified two read-only native workers with real Sarvam responses and
   overlapping provider streams. The first strict answer check failed for a
   missing project name; its original report remains alongside a separately
   recorded rerun after clarifying the task, with the same assertion.
6. Fixed Desktop generation-scoped, non-blocking shutdown and stale reconnect
   state; added fail-closed CLI model selection.
7. Added maintained runner/unit checks, bounded cleanup/reporting, and rebuilt
   the frontend/check-compiled the Tauri backend. Detailed results and limits
   remain in `VERIFICATION_2026-10-08.md`.

Live checks used synthetic files, not private repository code, user documents
or Syntarus uploads. This is narrow evidence of a working engine, not a claim
of industry-level general task success.

## Next implementation sequence

1. Finish native Desktop acceptance: actual UI, permission prompts, stop,
   reconnect, resumed history and honest failure presentation.
2. Prove multi-file coding workers in isolated worktrees and recovery after a
   worker/process is killed; exercise long-history compaction with real model
   capability metadata.
3. Port useful Smara tool primitives to MCP: search/fetch/extraction/citations,
   browser actions, local memory and approved Syntarus calls. Do not port the
   old autonomous planner as a second agent authority.
4. Restore schedules through native sessions, with explicit scopes, budgets,
   approval requirements and protection against repeated business actions.
5. Run broader suites, package native binaries and the Desktop installer,
   visually verify it, then perform an authorized install/publish cutover.

Company-operation autonomy comes after these integration and safety gates. It
is not automatically provided by copying a coding harness.
