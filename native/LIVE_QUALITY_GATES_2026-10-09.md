# Follow-up: real workers, Desktop visual checks and frozen browser

## What passed

The real Sarvam GLM 5.3 parent delegated two **fresh isolated coding workers**
on a disposable committed Python fixture. Each inspected and reproduced its
failing suite, patched only its assigned implementation, left both test files
and the other implementation unchanged, and reran its suite successfully.
An independent combined run passed all eight unchanged tests. The parent
checkout stayed unchanged; nothing was merged into Smara or another user repo.
Three provider response streams overlapped. No private source/history, personal
browser state or remote memory was used.

The first run was stopped by the acceptance client's conservative budget guard.
Both original failed turns remain recorded. A fresh native process resumed the
same parent and original registered worker checkouts without replaying inference;
the model resumed both workers, collected their actual final handoffs and closed
them. Their latest turns completed. Two native command approvals for recursive
fixture-cache cleanup were denied; the workers completed without those grants.

| Gate | Result | Evidence under ignored `build/` |
| --- | --- | --- |
| Real isolated workers + cold recovery | Passed; parent unchanged, all 8 tests pass | `native-live-workers-browser-20261009-continuation.json`, workers section |
| Real-model public DOM browser | Passed all 8 checks in 3 requests; answer independently reviewed against the retrieved primary documentation | `native-live-workers-browser-20261009-browser.json`, browser section |
| First real run | Retained interrupted/failed result, not relabeled a pass | `native-live-workers-browser-20261009-attempt1.json` |
| Full Python regression | 1,312 passed, 2 skipped, 561.51 seconds | `native-next-full-20261009.xml` |
| Latest acceptance-runner tests | 24 passed, including the offline clock request-only/no-grant guard and exact durable denial validation | `tests/test_native_live_quality.py`; separately rerun after full-suite collection |
| Latest focused native regression | 97 passed, 1 Windows symlink-privilege skip, 15.85 seconds | `native-durable-clock-all-20261009.xml`; earlier 90-test result also retained |
| Desktop source unit tests/build | 25 passed; production TypeScript/Vite build passed | `npm test`, `npm run build` |
| Source native Desktop debug build | Passed, locked/offline | `apps/desktop/src-tauri/target/debug/smara-desktop.exe` |
| Installed v0.1.9 visual protocol check | Send, active streaming, Stop, Disconnect, reconnect and same-thread resume passed | `native-desktop-installed-offline-f12ffd7e1ea1422e9af1cf8557f74107/stop-report.json` |
| New source UI visual protocol check | Same checks passed; new opt-ins visible and left off | `native-desktop-offline-17ec47fec1a146dea0434c2c70c7a488/stop-report.json` |
| Frozen CLI browser/driver | All 6 checks passed; actual page, grounding, cached paging, owned close | `frozen-browser-check-0812c1aa41594b29a86bfec11efb156a/report.json` |
| Latest pre-push secret pattern gate | Zero unclassified findings/skips; 153 reviewed public/synthetic matches, 11,843 reachable blobs and 11,594 workspace files considered | `secret-audit-clock-ledger-fix-pre-push-20261009.json` |

Computer Use capture recovered after explicitly activating the selected Smara
window. These were actual Windows app interactions, not a DOM-only substitute.
Each GUI used a new isolated state folder and declared **offline stream fixture**,
not Sarvam or the user's real conversation. Ledger/counter checks independently
show one request, one interruption, no false completion and no inference replay.
The generated reports honestly retain `gui_actions_machine_verified: false`;
the visual observations are documented here, not manufactured by the runner.
No security/permission controls were clicked by the assistant. Only the two
owned fixture windows and their local development server were closed; the user's
normal installed app and state were left alone.

The frozen CLI probe was built separately, not copied over an installed CLI.
Its 61.16 MB archive contains Playwright and its Node driver, and none of pandas,
pyarrow, datasets, pytest, Tk or numpy. SHA-256:
`b95d17a4c484748bbe0db13924a5d2c152b39db2ea8cfd7e412af1ab8a480c59`.
This does **not** verify the optimized native executables or frozen Desktop
executor/NSIS installer. Public-origin guards are not a complete OS/DNS escape
certification. DOM text does not establish screenshot computer-use capability.

The real-model browser task opened the actual public
[Python `heapq` documentation](https://docs.python.org/3/library/heapq.html)
in a fresh owned context. Manual answer review confirmed both operations and
the requested example: for `[5, 9, 12]` with incoming `3`, `heappushpop` returns
`3` and leaves the heap unchanged; `heapreplace` returns `5` and retains `3`.
The answer cites the retrieved document. No shell-fetch substitute, file changes,
personal cookies or interactive form actions occurred. This proves this bounded
public read/navigation task, not general browser or computer-use autonomy.
The generated report retains `semantic_answer_review_required: true`; this
separate manual review does not rewrite that original machine evidence.

## Budget/accounting — not an invoice

The user approved a new **INR 300 / 26-request** ceiling for these two checks.
The initial UTF-8-byte/input plus maximum-output reservation hit INR 291.11 after
14 requests. No extra outbound request crossed that guard. Native retry attempts
were local and unpaid, and exposed an acceptance-runner stop inefficiency.

The runner now stops immediately at the shared boundary and settles reservations
only for complete provider streams with valid token telemetry. Missing/error
telemetry retains its full reservation. All tokens use the highest verified
[Sarvam GLM rate](https://docs.sarvam.ai/api/getting-started/models/openweight/glm-5-3)
plus conservative framing; cache discounts are not assumed. Existing fixture
ledgers contained valid cumulative/incremental usage for all 14 requests, so
continuation carried those requests and verified usage forward. The earlier
INR 600 question was superseded; **no increase was used**.

At the end of the successful worker continuation, 26 cumulative requests had
used 276,821 reported tokens with an INR 120.17 conservative estimate. Its
combined report remains **failed/incomplete** because no paid browser request
fit the initial request ceiling; that evidence is retained.

The user then explicitly approved up to six additional **browser-only** requests
within the same INR 300 ceiling. The final combined report passes, carrying the
previous worker result forward rather than rerunning it: **29 of 32 permitted
cumulative requests**, 319,878 reported tokens, **INR 138.44 conservative
estimate**. Actual provider billing remains unknown. The late INR 600 approval
was not needed or used; the tighter INR 300 ceiling remained in force.

## Remaining gates, in order

1. Human **Deny** visual check. The assistant may inspect the UI but cannot click
   security approvals or opt-in controls. Existing native/response-helper
   deny/allow-once/deny mechanism evidence is not a human GUI pass.
2. Build and verify the optimized native payload, frozen Desktop executor and
   installer together, then install only the verified candidate with backup and
   state preservation. No new installation, version bump or public release was
   performed in this follow-up.
3. The user has now authorized committing/pushing this verified runner and
   documentation batch, plus building and installing after the remaining gates.
   Authorization is not a claim that the human Deny check or installation passed.
   Source batch `2778dec` was pushed atomically to both `origin/main` and
   `origin/codex/source-runtime-migration`; both GitHub secret-audit jobs passed.
4. Next feature work remains vision-capable disposable computer use, then native
   claim/source review and paused-by-default research refresh outcomes. No
   personal-login browsing, host automation or company-write autonomy is implied.

## Reproduction

Select the source runtime explicitly; the installed/release binary is older:

```powershell
$env:SMARA_NATIVE_BINARY='C:\Users\sujal\smara\vendor\codex\codex-rs\target\debug\smara-native.exe'
python -m scripts.run_native_live_workers_browser --report build/new-live-report.json
```

This makes paid provider calls and requires an explicit scope/budget approval.
Reports must use a new filename. `--continue-report` cold-resumes the same fixture
and carries cumulative request/cost reservations forward. The separately
authorized `--extra-browser-requests 6` mode requires a passed worker section and
runs only the browser task; it is not permission to reset the budget.

The offline GUI runner now creates unique fixture directories so older failures,
session state and reports are never overwritten. Failed/live fixture artifacts
are retained for diagnosis and are not release assets.

For the outstanding **human** Deny gate, `--offline-clock-deny` starts a declared
local provider fixture that requests only the actual native MCP `current_time`
tool and then reports the observed result. Its isolated configuration requires
approval; it neither enables readers nor responds to permissions automatically.
The human must enable readers, connect, send a test prompt and click Deny.
On GUI close, `clock-deny-report.json` verifies the real native ledger and local
provider result, without claiming the GUI action was machine verified. This is
a mechanism/UI check with zero paid calls, not a substituted real-model result.
The first clock GUI fixture was closed without making any request; its failed
report is preserved under
`native-desktop-offline-38c39aac80374d909b085998dd8ade3f/clock-deny-report.json`.
It is **not** evidence of a human Deny pass. A new packaged-candidate fixture
will be used for the outstanding human check.
Before that rerun, the fixture ledger reader was corrected to inspect the
persisted `item_completed` / `McpToolCall` item, rather than a live transport
notification that native rollouts do not store. It requires the exact rejected
clock scope, failed status, no result and native rejection error; other failures
do not count as a Deny. Failed fixture checks now also return a nonzero exit code.
