# Changes review and command output

These controls belong to Smara's native Desktop and CLI. They do not delegate
to a model, call a network service or revive the archived Python agent engine.
They require Git on PATH and the exact Git root registered in Settings → Projects.
Selecting a subfolder never grants access to its parent repository.

## Desktop

Open **Changes** in the chat header:

- **Unstaged**: inspect file/hunk patches, stage selected changes, or revert them.
- **Staged**: inspect and unstage selected changes without changing working files.
- **Last turn**: inspect the local before/after patch and Undo all, a file or a
  supported hunk. Undo changes files, not conversation history or the Git index.
- **Branch**: read-only committed changes from the common ancestor with a local
  comparison branch. It never fetches and does not include uncommitted edits.

Every write asks for confirmation. Writes are blocked while this Desktop runtime
has pending, active or uncertain native turns/workers. Reviewed file contents,
index entries and HEAD are checked again before applying. Stale reviews require
a refresh. Git checks the entire selected patch before applying it; conflicting
later changes refuse the batch instead of overwriting them. No uncertain write
is retried automatically.

New Git-root chat turns capture a local baseline before submission and seal it
after completion. Only existing working changes are copied; unchanged baseline
files come from immutable local Git blobs. Pre-existing edits and non-conflicting
later manual edits survive Undo. All edits during the turn are included, even
those made by another editor; this is not perfect attribution to the agent.
Committing during a turn makes that turn's Undo unavailable. Unsupported captures
do not prevent an ordinary chat response.

Revert and Undo retain original file bytes and a recovery receipt locally under
the state directory's `native-changes/` folder (normally `%APPDATA%/Smara/`).
**Restore last recovery** is available until the panel closes. The saved receipt
also works through the CLI. Restoration refuses files/index entries changed since
the action. Copies remain on disk for manual recovery; they are not sent to a
model, put in Git or automatically deleted. They can contain private code, so
treat this folder like private workspace data. If a helper crashes during a
write, refresh and inspect first; automatic restoration is not guaranteed.

**Agent terminal** shows command, working folder, streaming output, exit status
and duration from this chat's native command events. Nonzero exit codes are
shown as failures. Output is scrollable, not clipped by the presentation layer.
This is a command transcript, not a new interactive PTY: ask Smara in the composer
to run commands, with the existing native sandbox and approval rules.

## CLI

`smara changes` works without a model or model key. Run it only when other agent
sessions and editors are idle: separate CLI processes cannot inspect the Desktop's
in-memory activity guard. Tokens still reject changed files/index/HEAD, but this
is not an OS-level interprocess filesystem transaction.

```powershell
smara changes status --workspace C:\work\project
smara changes file --workspace C:\work\project --token TOKEN --path src/main.py
smara changes apply --workspace C:\work\project --token TOKEN --path src/main.py --hunk 0 --action stage --yes
smara changes status --workspace C:\work\project --view staged --json
smara changes status --workspace C:\work\project --view turn --thread-id THREAD
smara changes status --workspace C:\work\project --view branch --base main
smara changes recover --workspace C:\work\project --recovery RECEIPT --yes
```

Use a fresh token from the matching view. Hunk indexes are zero-based; omit
`--hunk` for a whole file and omit both `--path`/`--hunk` for all reviewed files.
Tokens expire after 30 minutes. `--json` is available for automation.

## Explicit limits

Binary, non-UTF-8, symlink/junction, submodule and executable-filter files are
excluded. Conflicted files require manual resolution. New/deleted files and
mode-changing patches require whole-file actions. Turn snapshots track text
content, not permission-only changes. Ignored files are not captured.

Review limits are 1,000 changed paths, 1 MB per text file, 200 KB per file patch
and 8 MB per snapshot/recovery batch. Exclusions are reported; content is never
silently truncated or claimed fully undoable. These are local review limits,
not new message-length or model-context limits. Use an editor for excluded files.

## Verification boundary

Tests use real temporary Git repositories and actual pytest exit codes, plus
synthetic native event/component fixtures. Visual QA uses a separate synthetic
project and no model/key in source and installed Windows Desktops. These checks
are not a paid-model evaluation, a full installed-agent acceptance suite or full
Codex parity.

Checked on 2026-10-11:

- Python regression run: 1,406 passed, 3 skipped. Final Git/CLI-focused rerun
  after the concurrency/CLI fixes: 55 passed, 1 skipped (symlink creation unavailable).
- Final frontend: 47 passed; TypeScript and production Vite build passed.
- Native Desktop review activity guard: 4 tests passed; debug build passed.
- Rebuilt portable CLI: actual status → patch → stage → staged review → unstage
  against the isolated synthetic Git project passed. No model/provider calls.
- Separately frozen Desktop companion: real stdin/JSON status and file-diff IPC
  passed without source fallback. Archive inspection confirms the review helper
  is included and all eight archived execution modules remain excluded.
- Windows visual check: real diff, file/hunk controls and panel switching passed
  in the source candidate. It found and led to fixing concurrent-preview locking
  and the unconfigured-model startup status. No GUI write or approval button was
  operated; write behavior is covered by real-Git fixtures and the portable CLI.
- Changed/added implementation files: redacted credential-pattern scan had no
  matches. This is not a fresh audit of GitHub history or release assets.

Release verification adds the installed Windows diff/file/hunk controls,
Agent terminal pane and Help page, payload/version hashes, state-preserving
installation, and a fresh read-only secret gate. See
[v0.1.12 installation evidence](VERIFICATION_2026-10-11_REVIEW.md).

For repeatable checks, run `pytest tests/test_native_changes.py` from the source
environment, `npm test` / `npm run build` in `apps/desktop`, and
`cargo test --bin smara-desktop review_tests` in `apps/desktop/src-tauri`.
`scripts/prepare_changes_desktop_fixture.py` opens a separate synthetic project
with no model or key; by default it requires the source debug Desktop and Vite
dev server. Pass `--installed` to use the actual installed executable and its
packaged companion. It never installs or changes your normal state.
