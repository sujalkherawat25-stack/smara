# Smara

Smara is the independent agent control plane. It owns tasks, approvals, task
events, desktop execution requests, and public research. Private integrations
run on the paired desktop rather than the hosted VM. Long-term memory is accessed **only** via
the public `syntarus` SDK; this repository must never import MemoryOS internals
or connect to Qdrant/Neo4j directly.

## Services

- `api`: task and approval HTTP API.
- `worker`: claims durable task steps and invokes an executor.
- `scheduler`: creates due task runs (the initial implementation is deliberately
  small; recurring schedules are added through the same task API).
- `integration-worker`: a disabled-by-default compatibility worker for approved,
  idempotent provider actions. In the hosted control-plane deployment it stays
  idle and never decrypts user credentials; private integrations run on the
  paired desktop instead.

For local development, leave `SMARA_DATABASE_URL` empty and set
`SMARA_DATABASE_PATH=./data/smara.db`.
Production must use a Postgres-backed store before multiple API/worker replicas
are deployed. Redis is for event fan-out only, never the sole task source of
truth.

## Canonical web app and phone companion

The task-first Smara web app is served at the canonical `ai.syntarus.com/`
root by the frontend container. It works responsively on a phone and is
installable as a small PWA companion for task status and approvals. The
`/smara-api/` path is API-only; it has no browser control UI. The former
`/smara/` and `/smara-api/app/` surfaces are retired so users cannot land in a
stale duplicate shell. It does not run a phone executor. In development,
enter a local account identifier in the sidebar; production requests rely on
the signed identity gateway and do not expose an account identifier in browser
storage.

Phone alerts need HTTPS plus the VAPID keys in `.env.example`. Once enabled,
the installed app can register its own browser-push subscription and Smara
sends an approval alert when an external action enters review. Quick text,
voice, and photo captures create inbox tasks. Media is retained in Smara and is
never sent to a provider unless the deployment explicitly configures the
matching capture provider. With `SMARA_CAPTURE_TRANSCRIPTION_*` configured,
voice creates a bounded transcript artifact; with
`SMARA_CAPTURE_VISION_*` configured, photos create a bounded description
artifact. PDF/image document captures can use Sarvam Document AI through
`SMARA_CAPTURE_OCR_*` and create a bounded Markdown OCR artifact. Without an
optional provider, the capture task still completes with a clear
"not configured" result, so a missing provider cannot block the phone
companion.

## Operator console and product boundaries

The private operator console is available at `/admin` with a separately
provisioned `SMARA_OPERATOR_SECRET`. It has its own short-lived HttpOnly
operator session, independent of normal Smara user sign-in. It is a Smara-owned
dashboard for task health, executors, account metadata, artifacts, events, and
connection status. It does not expose provider keys, OAuth grants, task
objectives, final answers, or raw memory by default.

The console keeps the products visibly separate: **Smara control plane** shows
durable tasks, approvals, executors, integrations, events, and account access
metadata from Smara's store; **Syntarus context plane** shows only an
independent SDK/API health probe and boundary status. Syntarus memory remains
in Syntarus and is never copied into Smara's task database.

The historical `/v1/memento/admin/dashboard` bookmark redirects to `/admin`
when the updated Caddy configuration is deployed. Other retired Memento routes
remain `410 Gone`.

## Memory boundary

`src/smara/syntarus_adapter.py` is the only memory integration point. Give the
service a server-side Syntarus project key; never send it to web, phone, or
desktop clients.

## CLI and extracted agent runtime

`smara` is a thin client of this API, not a second local agent or memory store.
It is the first migration surface for Memento's agent behaviour and lets us
test the same task graph used by Web, Desktop, and Phone:

```powershell
smara --api http://127.0.0.1:8080 --dev-account acct_local tasks
smara --api http://127.0.0.1:8080 --dev-account acct_local tasks list
smara --api http://127.0.0.1:8080 --dev-account acct_local run "Prepare a cited report"
smara --api http://127.0.0.1:8080 --dev-account acct_local task watch task_xxx
# Preferred: browser-based device authorization (opens Smara Web and waits for approval)
smara --api https://smara.example.com login
# Legacy fallback: after Smara Web displays a one-time pairing code:
smara --api https://smara.example.com login smara_<one-time-code>
smara --api https://smara.example.com --token <cli-token> tasks
smara --api https://smara.example.com chat --session work
smara --api https://smara.example.com chat -q "Summarize my current tasks" --model-profile cheap
smara --api https://smara.example.com plugins
```

`smara login` uses a short-lived device code, opens the canonical Smara root,
and polls
until the signed-in Smara Web session approves that device. No bearer token is
shown or copied. If the browser cannot be opened automatically, the command
prints the approval URL. `smara login <one-time-code>` remains a compatibility
fallback and both flows save the scoped bearer in the user CLI config
directory; use `--print-token` only with the legacy flow when a script
explicitly needs it.
`smara logout` revokes the current CLI device on the server and removes its
local token. `smara devices` lists active CLI devices and
`smara devices revoke <device-id>` revokes another device. Legacy tokens issued
before the device registry remain usable until their normal expiry and become
revocable after the next login. `task watch` resumes after a dropped SSE
connection using the last durable event ID.

`smara chat` is an interactive streaming client. Session names map to stable,
account-scoped conversations. Successful user/assistant exchanges are persisted
on the hosted service, recent turns are restored on resume, and older turns are
compacted into a bounded rolling summary. `/new NAME`, `/sessions`, `/history`,
`/approvals`, `/approve ID`, `/deny ID`, `/devices`, `/revoke ID`, and `/exit`
are supported. Model profiles are operator allowlists configured with
`SMARA_LLM_PROFILES` (JSON) and `SMARA_LLM_DEFAULT_PROFILE`; profile secrets
should use `api_key_env` and are never returned by the API.

`smara ask "..."` is a bounded direct conversation. It requires a configured
OpenAI-compatible provider (`SMARA_LLM_*`), retrieves context only through the
Syntarus SDK adapter, and may use the same bounded read-only tools as hosted
task steps (calculation, research search/retrieval, and configured read-only
integrations). Work that takes time, creates an artifact, or needs approval
must use `smara run` so it survives a closed terminal or browser. Direct chat
can never send, edit, delete, execute local commands, or bypass an approval.

The pairing flow is deliberately two-part: an authenticated Smara Web session
starts the code, and the terminal exchanges it once. Smara
stores only hashes of pairing codes and issued device IDs; the CLI bearer uses
a separate signing secret and is accepted only for the `smara-cli` audience.
Issued devices can be listed and revoked without exposing their bearer or raw
JWT identifier.

`POST /v1/chat/stream` provides the same bounded turn as safe Server-Sent
Events for Web and CLI clients. Its stable event names (`phase`, `status`,
`tool_call`, `tool_result`, `token`, `done`, `error`) are adapted from
Memento's proven contract. They expose useful progress but never model
chain-of-thought, provider credentials, or raw upstream error strings.

The provider-neutral safe tool registry is available at `GET /v1/tools`. The
initial tools are UTC time, bounded arithmetic, public research search, and
SSRF-safe URL retrieval. Read-only tools may be invoked through
`POST /v1/tools/{name}`; side-effecting tools are deliberately rejected there
and must run as approved durable tasks.

The CLI is the same hosted client: `smara tools`, `smara tool calculate
--arguments '{"expression":"2+2"}'`, `smara desktop list`, and
`smara desktop pair`. It stores no task or memory database locally.

### Persistent desktop executor

The independent `smara-desktop` command is the local executor. It makes only
outbound HTTPS requests, stores its paired device token in the operating
system's Smara config directory, and waits for a hosted lease before doing
anything. Start with the least-privileged pairing:

```powershell
smara desktop pair --capability local_file_read
smara-desktop --pair ABCD1234 --api https://smara.example.com --allow-root C:\Users\you\Documents
```

The desktop currently supports bounded `local_file_read` (hash/proof by
default, content only when the approved payload explicitly requests it),
reviewable `local_file_write` (read-only previews, write/append/patch,
rename/move, delete-to-undo, atomic replacement, and guarded local undo),
allowlisted `local_terminal`, allowlisted `local_browser`, and the optional
`local_integration` adapter. Browser work is
explicitly local: `open` launches the system browser, `inspect_text` returns
bounded page text, `inspect_dom` returns a small semantic or selector-based DOM
summary, and `download` streams a file into an approved folder (50 MB cap).
Inspection and downloads never send browser cookies, reject redirects, and
return a source URL plus SHA-256 proof metadata.
When `local_integration` is explicitly paired, the desktop can run approved
read-only Tavily search and GitHub repository-list requests using
`TAVILY_API_KEY` or `GITHUB_TOKEN` from its encrypted vault. The key is sent
directly to that provider, never to Smara, and is never included in results.
Terminal and browser capabilities must be
declared during pairing and configured locally with `--terminal-allow` or
`--browser-domain`; the executor rejects undeclared capabilities, unapproved
tasks, shell operators, path traversal, symlink escapes, oversized files, and
unbounded commands, and ambiguous patches. Every write mutation computes and
returns a bounded diff before applying it, while local undo snapshots stay in
the user's app-data directory and never cross the hosted boundary. Each
completed local step creates a visible task-result artifact. On Windows the
scoped executor bearer is protected with the current user's DPAPI key instead
of being stored as readable JSON. A single-instance lock prevents duplicate
runners, and bounded rotating logs are stored under the user's local Smara
directory.

`--once` is available for a single-step smoke test. Operational controls are:

```powershell
smara-desktop --status
smara-desktop --pause
smara-desktop --resume
smara desktop list
smara desktop revoke desktop_ID
```

After pairing is verified, `scripts/install-smara-desktop.ps1` registers the
executor for the current Windows user at sign-in with limited privileges and
duplicate-instance prevention. `scripts/uninstall-smara-desktop.ps1` removes
auto-start; revoke the executor separately to invalidate its server token.

The executor claims a three-minute desktop step lease by default and calls
`POST /v1/executors/steps/{step_id}/heartbeat` immediately before completion.
The refresh is account- and executor-scoped; a cancelled step is not extended,
and a stale executor cannot finalize work recovered by another paired device.

For repeatable workspace checks, an approved `local_terminal` step may use one
of the fixed recipes `python.test`, `python.compile`, `node.test`,
`node.build`, `rust.test`, `rust.check`, or `git.diff-check` instead of an
`argv` list. Recipes remain subject to the local executable allowlist and do
not accept shell operators or extra flags. A recipe can name up to 20 explicit
workspace artifact paths; the result returns only bounded path/size/hash
metadata. When Git is allowlisted, the result also reports files whose status
changed during the run. Desktop Activity renders these results, output, and
edit diffs in expandable sections.

The hosted control plane intentionally does not advertise Gmail, Calendar,
Drive, GitHub, Telegram, or other personal-account tools. Their credentials and
browser sessions remain on the paired desktop, where a future local adapter
will turn an approved request into a local action and return only a bounded
result/proof. Public research tools are different: they fetch public URLs with
the operator's server-side search key and cannot see a user's browser session.

### Native Smara Desktop (beta)

`apps/desktop` contains the Windows-native Tauri companion for this executor.
It provides one lightweight home for hosted chat, task status, local activity,
pairing, permissions, pause/resume, logs, and revoke. It reuses the Python
executor and the same hosted API; it does not create a second agent brain or
memory store. See [`apps/desktop/README.md`](apps/desktop/README.md) for the
developer run/build commands and the current packaging boundary.

The native Smara Telegram worker uses the same account database and one-time
link codes; it calls the Smara chat API on the private Docker network. It is
the only Telegram poller in the live deployment. The old Memento worker was
removed, and its public agent/auth routes return `410 Gone`; the source remains
only as a rollback/reference asset in MemoryOS. `SMARA_HOSTED_USER_INTEGRATIONS_ENABLED=false`
keeps personal credential storage disabled. Never put a user's provider token
in the hosted `.env` or Smara Postgres.

## First research workflow

`POST /v1/research` accepts a question and optionally up to twelve explicitly
supplied public HTTP(S) source URLs. If no URLs are supplied, the first task
step uses the configured provider-neutral search adapter to discover public
sources before retrieval. Configure `SMARA_SEARCH_PROVIDER`,
`SMARA_SEARCH_API_KEY`, and (optionally) `SMARA_SEARCH_URL` on the server. The
provider can be `brave`, `serper`, `tavily`, or `exa`; when the URL is omitted
Smara selects the provider's official endpoint. For resilient deep research,
set `SMARA_SEARCH_FALLBACK_PROVIDER` and `SMARA_SEARCH_FALLBACK_API_KEY` to a
second provider. The keys never leave the API process. Chat requests that ask
for detailed analysis, current developments, comparisons, or citations use a
deterministic research pass: several distinct search angles run in parallel,
results are deduplicated and diversified by domain, and the strongest pages
are fetched concurrently. Each answer is written from labelled evidence; a
blocked page remains visible as an explicitly unverified search snippet. The
worker retrieves only safe public text/HTML
sources, records failures rather than inventing content, hashes each retrieved
source, verifies the usable evidence, and writes a cited Markdown artifact.

- `GET /v1/research/{task_id}/evidence` returns the durable evidence ledger.
- `GET /v1/tasks/{task_id}/artifacts` returns the cited report artifact.
- `smara research "question"` creates a research task; `smara task evidence <id>`
  prints its source ledger from PowerShell or a terminal.

Each verified source also records transparent quality signals: publication-date
presence, HTTPS use, configured domain policy, and deterministic token-level
agreement with other retrieved sources. These signals are advisory and remain
visible in the Web/CLI; they never become hidden claims or bypass approval.

Task progress is durable and can be followed live with
`GET /v1/tasks/{task_id}/events/stream`. The Web task board listens to this SSE
stream and the CLI `task watch` command uses it too. Each update has an event
ID; Web reconnects send `Last-Event-ID` so a dropped connection resumes from
the durable ledger instead of silently skipping progress.

Schedules use the same task graph rather than a second execution engine. Create
one with `POST /v1/schedules` and an `interval_seconds` value between 60 and
2,592,000; the separate scheduler process creates a normal task run when due.
Each run keeps its own approval setting, and stopping a schedule prevents future
runs without altering completed history.

This slice does not use an LLM to infer claims or deliver reports externally.
Those remain separate, approval-gated capabilities. An optional
`SMARA_RESEARCH_SYNTHESIS_ENABLED=true` setting enables bounded synthesis using
the configured OpenAI-compatible model, but only after evidence is verified.
Every model citation must match the ledger; invalid, unavailable, or
unconfigured synthesis safely falls back to the deterministic cited report.

Capture providers use independent endpoints and may reuse the operator's
server-side `SMARA_SARVAM_KEY` through the documented secret fallback. They are
never sent to web, CLI, or desktop clients. Set all three variables in each
group to enable a provider (for example, an OpenAI-compatible endpoint):
`SMARA_CAPTURE_TRANSCRIPTION_BASE_URL`,
`SMARA_CAPTURE_TRANSCRIPTION_API_KEY`,
`SMARA_CAPTURE_TRANSCRIPTION_MODEL`; or
`SMARA_CAPTURE_VISION_BASE_URL`, `SMARA_CAPTURE_VISION_API_KEY`,
`SMARA_CAPTURE_VISION_MODEL`. For Sarvam, use `/v2` + `gemma4` with the
`api-subscription-key` header for image descriptions. For OCR, use
`SMARA_CAPTURE_OCR_BASE_URL=https://api.sarvam.ai/doc-ai/v1`,
`SMARA_CAPTURE_OCR_MODEL=sarvam-vision-v1`, and
`SMARA_CAPTURE_OCR_AUTH_HEADER=api-subscription-key`; the worker submits a
bounded job, polls it, downloads the result, and stores only extracted text.
Upload limits remain 10 MB for voice, 4 MB for photos, and 20 MB for documents;
provider errors are recorded through the normal bounded retry path.

The hosted model profile example includes Sarvam `sarvam-105b` on `/v1`,
`glm5.2` on `/v2` for deeper reasoning, and `gemma4` on `/v2` for image input.
Only profile names cross the client boundary. GLM-5.2 and Gemma 4 require
Sarvam beta access; OCR is a separate Document AI workflow rather than a chat
profile.

## Integrations and approvals

Connections are account-scoped and have one policy: `observe`, `draft`,
`assisted`, `trusted`, or `blocked`. External actions always create a durable
intent and visible preview first. Until a bounded trusted-workflow template is
added, even `trusted` external actions require approval. An approver can edit
the preview and action payload before authorizing it.

The legacy hosted credential endpoint stores only Fernet-encrypted ciphertext in
Postgres and is disabled in the default deployment. If it is ever enabled for
a reviewed migration, set `SMARA_HOSTED_USER_INTEGRATIONS_ENABLED=true` and
inject `SMARA_INTEGRATION_MASTER_KEYS` from a production secret manager; it is
an ordered key ring that supports no-downtime rotation. This is not the normal
user path: personal OAuth/API tokens belong on the paired desktop.

No provider credential or OAuth application secret is included in this
repository. That is intentional: configure them in the deployment environment
before using a live connection.

## Operations and isolated execution

`GET /readyz` checks the task database; `/health` only confirms the process is
alive. Terminal task failures are retained in `GET /v1/dead-letters` for human
review rather than disappearing after retries. The API has an abuse limit and
safe browser headers; deploy a gateway/WAF in front of it for distributed rate
limits.

For a production schema migration, use the same image and database URL as the
services: `smara-migrate` reports applied/pending versions, while
`smara-migrate --apply` applies only pending SQL files. It never prints the
database URL or copies MemoryOS data. Run it before the API/worker rollout and
keep the previous deployment available for rollback.

Use `scripts/backup-postgres.sh` for a nightly encrypted-storage database
backup and `scripts/verify-backup.sh backup.dump` to validate an archive. A
release is not complete until a documented restore is exercised into a fresh,
disposable Postgres database.

`smara.sandbox` is an internal-only recipe for future approved code/repository
steps. It uses a fresh Docker container with no network, no mounts, no inherited
environment, a read-only root filesystem, dropped capabilities and bounded CPU,
memory, process count and lifetime. It is not available through a public API.
