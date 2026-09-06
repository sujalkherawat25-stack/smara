# Smara Phone product vision

## In simple language

Smara Phone is one personal agent that can remember across devices and do useful work on the phone. It can research, explain, draft, remember people and projects, ask before risky actions, schedule work, and gradually reuse successful workflows. The screen shows the active workers and steps instead of pretending everything is one chat bubble.

The first working slice is the current Smara experience on Android: sign in, ask, recall Syntarus memory, run cloud tools, stream the response, and approve side effects. Phone-native actions are added adapter by adapter so permissions remain understandable.

## High-level technical shape

```text
Android Compose UI
  -> local task graph and capability policy
     -> safe phone adapters (intents, calendar, files, notifications)
     -> confirmation gate for side effects
     -> existing Smara SSE agent
        -> research, documents, reasoning, and cloud tools
        -> Syntarus Memory (Qdrant + graph + cache)
```

Browser authorization produces the existing Smara account session. The backend resolves that session to the same `account_id` used by the website and Telegram. Every durable memory therefore belongs to one account rather than one browser or phone.

## Capability roadmap

### Working foundation

- Native Android UI and visible task execution.
- Smara-hosted browser account authorization.
- Encrypted session storage through Android Keystore.
- Live Smara SSE phases, memory searches, tools, tokens, and confirmations.
- Local fallback planner when signed out.
- Built-in declarative skills and reception of learned Syntarus workflows.

### Next phone adapters

- WorkManager reminders and durable background retries.
- Calendar read/create with narrow runtime access.
- User-selected documents through Android's Storage Access Framework.
- Contacts lookup only when a task explicitly needs a person.
- Share sheet, app links, notifications, and supported app intents.
- Push-to-talk voice, followed by clearly indicated hands-free mode.
- Room-backed, account-scoped recent task history.

## Controlled self-improvement

Smara should improve by learning **workflow manifests**, not by rewriting or downloading executable APK code.

1. The backend observes repeated successful tool sequences.
2. Syntarus activates a versioned account-scoped skill.
3. Android maps its declared tools to a small allowlist of capabilities.
4. Permissions and confirmation requirements are evaluated again every run.
5. Failed or degraded skills can be disabled or rolled back by version.

This provides Hermes-like reusable skills while retaining Android security. A skill can learn that a user prefers a certain research-and-reminder workflow; it cannot silently acquire contact access, send a message, make a payment, or execute an arbitrary shell payload.

## What “local” means

The phone owns interaction, local state, permissions, supported device actions, and lightweight planning. Complex reasoning and durable memory remain managed services in the initial release. This is intentional: a fully local frontier model would exclude lower-end phones and consume substantial battery, memory, and storage.

Later, an optional quantized 1–3B model can provide offline classification, drafting, and command parsing on 8 GB+ devices. It remains a fallback; Syntarus is still the cross-device memory source of truth.

## Android control boundary

Android does not provide a safe universal terminal that can silently control every app. Smara will prefer official APIs, intents, app links, and user-granted providers. Accessibility automation may be offered only as an explicit compatibility mode with visible state and per-action review. Root access is neither required nor supported.

## Product advantage over a generic phone agent

- One durable memory shared across phone, web, and Telegram.
- Account- and project-scoped evidence instead of device-local chat logs.
- Visible task graph and precise approval points.
- Skills that improve safely and can be versioned or rolled back.
- Hybrid execution that works on ordinary Android phones, not only flagship hardware.
