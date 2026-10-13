# Continuum memory — local v0.1.21 verification

## Delivered

Syntarus SDK recall and conversation capture are attached to the existing native
provider boundary shared by Desktop, CLI and goals. The Rust loop, approvals,
tools and procedural `SKILL.md` loader remain authoritative. Settings → Memory
adds protected key storage, explicit opt-in, project/personal scope, stable user
identity, status, a synthetic read-only probe and safe key removal.

The previous uninstalled PDF preflight changes are also included in this local
candidate. User-deleted reports and unrelated existing changes were preserved.
No Git commit, push, tag or public GitHub release was made for this update.

## Checks

- Final native Python lane: **381 passed, 2 skipped**.
- Desktop frontend: **86 passed**; TypeScript/Vite build passed.
- Desktop native Rust: **11 passed**; `cargo check --locked` passed.
- Actual source-native binary with public SDK/offline HTTP fixtures: **7/7**
  checks passed. Two separate root threads, cross-session recall, successful
  event polling, once-only capture, key confinement and file skill discovery.
- Frozen Desktop/CLI configuration and real Windows DPAPI: **13/13** passed.
  The SDK is bundled, metadata/UI never return keys and enabling is explicit.
- Frozen Desktop native execution: **13** protocol checks passed, including a
  real patch, native command rejection/non-execution, resume/list and shutdown.
- Installer built at
  `apps/desktop/src-tauri/target/release/bundle/nsis/Smara Desktop_0.1.21_x64-setup.exe`.
  SHA-256: `d868cb64f240be394c695463b3f0c6d849f52b68fb4359de0a96d2169d85dd1b`.
- Maintained installer verified installed payload hashes and retained all four
  protected settings/preferences file hashes. Previous Desktop is recoverable
  in its install-generated `Smara-backups/desktop-pre-0.1.21-*` directory.

Installed Desktop: `%LOCALAPPDATA%/Smara Desktop/smara-desktop.exe`, version
`0.1.21`. Matching CLI: `%LOCALAPPDATA%/Programs/Smara CLI/0.1.21/smara.exe`.
Open a new terminal for the updated user PATH.

## Scope and remaining checks

The synthetic SDK fixture uses the real public client methods and fixed API
path, but its HTTP transport is scripted and does **not** contact Syntarus.
No private history, real Syntarus request or paid model inference was sent.
The actual hosted write → successful event → cross-session recall test still
requires a saved project key with read/write scopes and explicit synthetic-test
approval. A connectivity probe alone does not certify write scope or recall.

Frontend interaction tests and compilation passed. Direct visual inspection of
the installed Windows GUI was not performed in this turn; do not equate the
component fixture with an installed-GUI check. Broad production/model quality
and OS-sandbox escape certification are not established by these checks.

The redacted repository audit scanned 12,088 Git blobs and considered 9,902 files
with zero skipped inputs. It reported 153 existing/history pattern findings
for review; no working-tree pattern findings were present in the changed
memory adapter/provider/runtime/manager/UI/test/acceptance files. This is not
a claim that all historical matches are classified or no secret can exist.

Existing local history is not automatically bulk-imported. Continuum is off
until the user explicitly enables it; personal scope deliberately shares
context across projects. Procedures remain local files, not hosted memories.
