# Smara Phone architecture

## Product rule

Smara Phone is a phone-resident agent with a cloud memory, not a smaller copy of the web chat. A request becomes a task graph. Each node declares the capability, permission, data boundary, and confirmation it needs before it can run.

## Execution layers

| Layer | Responsibility | Trust boundary |
|---|---|---|
| Compose UI | Request, progress, approvals, result, recent work | No secrets or direct tool execution |
| Agent orchestrator | Builds and advances the dependency graph | Cannot grant Android permissions |
| Capability registry | Describes availability and routing | Deny by default |
| Device adapters | Calendar, contacts, reminders, files, notifications | Android runtime permission + narrow data access |
| Cloud gateway | Streams the existing Smara/Memento agent | Encrypted account-scoped session |
| Syntarus Memory | Durable cross-device memory and evidence | Account/project-scoped server boundary |

## Execution rule

```text
request
  -> local intent plan
  -> dependency graph
  -> run safe/read nodes
  -> show approval for a side effect
  -> execute through an official Android API or intent
  -> store the outcome as an action episode
  -> render a focused result
```

Independent graph nodes can run concurrently. Dependent nodes run only after their inputs complete. A failed or denied node blocks only its dependants, not the entire task.

## Target execution architecture

The next agent core is a durable action graph rather than a long model loop:

```text
request
  -> deterministic fast router
  -> compact planner only when the request is ambiguous
  -> typed action graph
       read nodes run in parallel
       write nodes stop at the approval broker
       long nodes move to WorkManager
  -> evidence validator
  -> focused answer
  -> action outcome + reusable workflow saved to Syntarus
```

Each node has typed inputs and outputs, a timeout, retry policy, permission,
data boundary, idempotency key, and user-visible status. Obvious requests such
as "open my resume" bypass cloud planning entirely. This is the main latency
and reliability upgrade over repeatedly asking a model what to do next.

## Document and OCR lane

```text
selected PDF or camera image
  -> native text extraction (fastest)
  -> bundled on-device Latin + Devanagari OCR (private, offline)
  -> quality gate
  -> authenticated Smara upload only for weak/complex results
  -> existing Gemini scanned-PDF/layout OCR
  -> normal agent reasoning with attachment evidence
```

The phone never embeds a cloud API key. Large VLM document parsers belong on
a GPU service, not in the APK; their disk, RAM, heat, and startup costs are a
poor fit for mobile. The cloud fallback is account-scoped and explicit in the
execution UI.

## Identity and history

- Before sign-in, tasks use the small local planner and are not written to Syntarus.
- Browser authorization creates the same account session and account ID used by web and Telegram.
- Local task history must be stored under that account ID.
- Switching or signing out closes the active namespace; one account must never see another account's tasks.
- The APK never contains a long-lived Syntarus or model-provider key.

## Android capability policy

1. Prefer official Android APIs, app links, share sheets, and intents.
2. Ask for a runtime permission only at the moment a relevant task needs it.
3. Read only the minimum records needed for the current task.
4. Preview messages, payments, deletion, sharing, and account changes immediately before execution.
5. Treat AccessibilityService as an optional compatibility adapter, never a universal hidden control plane.

## Foundation status

Implemented now:

- Native app shell and Smara visual identity.
- Visible dependency-graph execution.
- Capability state and permission model.
- Local deterministic planner.
- Android Keystore-backed account session storage.
- DataStore settings and non-backup rules.
- OAuth-style browser device authorization and encrypted session restoration.
- Native cloud gateway matching Smara's phase/tool/token/approval stream.
- Declarative skill registry with a capability allowlist and risk classification.
- User-approved folder search and direct file opening.
- Background downloads, local reminders, calendar drafts, and task notifications.
- Camera/document OCR with a local-first quality gate and Smara cloud fallback.

## Memory path

```text
Short-lived device request
  -> Smara-hosted browser approval
  -> one-time device token exchange
  -> encrypted mem_session on Android
  -> POST /v1/memento/chat
  -> account_id
  -> existing Smara retrieval and background ingestion
  -> Qdrant + Neo4j + Redis under that account_id
```

The APK never calls Qdrant or Neo4j and never carries a public Memory API key. All tenancy and memory trust rules stay server-side.

Next production slice:

- Room-backed account-scoped activity and durable action-graph checkpoints.
- Calendar/contact read adapters, deep links, clipboard, Maps, and email drafts.
- A capability test harness that runs each phone adapter against a fake and a real Android provider before release.
