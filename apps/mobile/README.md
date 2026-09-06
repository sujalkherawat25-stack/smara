# Smara Phone

Native Android foundation for Smara as a phone-resident agent, not a chat wrapper.

## What exists in this first slice

- A Jetpack Compose agent interface with the Smara orb and a visible execution graph.
- A deterministic local planner that breaks requests into memory, research, reminder, and synthesis steps.
- A capability registry that distinguishes ready, permission-gated, account-paired, and future adapters.
- Browser-based Smara account linking and Android Keystore-backed sessions.
- Device-only settings through DataStore with cloud backup disabled for sensitive app state.
- A live native SSE gateway for Smara's phases, Syntarus memory, tools, approvals, and streamed answers.
- Unit tests for task planning and dependency construction.
- Local-first camera/PDF OCR (Latin + Devanagari) with an authenticated cloud
  fallback for difficult scans and document layouts.

The app deliberately does **not** embed a Syntarus API key or silently request broad Android permissions. It signs into the existing account-backed Smara path, so phone, web, and Telegram use the same Syntarus memory. Device actions remain permission-gated.

## Install the beta on a phone

The verified APK is built at:

```text
releases/Smara-ocr-hybrid.apk
```

Transfer that file to the phone, open it from Files or Downloads, and allow
"Install unknown apps" for that file manager when Android asks. The APK is a
debug-signed private beta intended for direct testing, not a Play Store release.

After installation, tap **Connect Smara**. The app opens `ai.syntarus.com` in
the browser, where the user approves the phone. Returning to the app completes
the one-time link; the session is then stored with Android Keystore encryption.

## Open and build

1. Install a current Android Studio release with Android SDK 36 and JDK 17.
2. Open the `smara_apk` directory as a project.
3. Let Android Studio sync Gradle dependencies.
4. Select an Android 8.0+ device or emulator and run the `app` configuration.

The verified Gradle 9.4.1 wrapper is included. From a terminal, run
`gradlew testDebugUnitTest assembleDebug lintDebug` on Windows or the equivalent
`./gradlew` command on macOS/Linux.

### Account connection

The APK creates a ten-minute, single-use device request and opens Smara in the user's browser. An existing Smara browser session can approve immediately; otherwise the Smara-hosted page handles sign-in. The APK receives the resulting account session by polling with a secret known only to that installation. No session token is placed in a browser URL.

No client secret, Syntarus key, or model-provider key belongs in the APK.

## Phone requirements

| Requirement | Minimum | Recommended |
|---|---:|---:|
| Android | Android 8.0 / API 26 | Android 12 or newer |
| RAM | 3 GB | 4 GB or more |
| Free storage | 150 MB for app and update headroom | 300 MB |
| Services | A modern browser | Chrome, Brave, Firefox, or the system browser |
| Network | Required for Smara reasoning and Syntarus memory | Stable 4G, 5G, or Wi-Fi |

An iQOO 13 is comfortably above these requirements. This first architecture does not run a giant language model continuously on the phone: local planning and Android actions are lightweight, while difficult reasoning runs on Smara's managed model path. That keeps it practical on older phones and avoids excessive heat and battery drain. An optional small offline model can be added later for devices with at least 8 GB RAM, without changing durable Syntarus memory.

## Architectural boundary

```text
UI (Compose)
  -> AgentOrchestrator
      -> LocalIntentPlanner
      -> CapabilityRegistry
          -> on-device adapters (permission-scoped)
          -> Smara cloud gateway (account-scoped)
              -> Memento agent tools
              -> Syntarus Memory API
```

The phone is the execution surface. Syntarus remains the durable memory and identity layer. Risky actions must show an approval card immediately before execution; the planner never grants itself permission.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for identity, execution, and Android capability boundaries.
See [docs/PRODUCT_VISION.md](docs/PRODUCT_VISION.md) for the complete product and controlled-learning plan.

## Next implementation slices

1. **Durable action graph:** Room-backed task checkpoints, typed adapter results,
   idempotency, retry/dead-letter state, and account-scoped recent work.
2. **Broader official adapters:** calendar/contact reads, Maps, clipboard, email
   drafts, app deep links, and MediaStore search with just-in-time permission.
3. **Fast routing:** deterministic command lane first, small planner second,
   frontier cloud reasoning only for genuinely hard multi-step requests.
4. **Voice:** push-to-talk first, then carefully gated hands-free mode with clear microphone state.
5. **Controlled automation:** AccessibilityService only as an optional last resort for apps without supported intents/APIs, with per-action preview and approval.

## Security rules

- Never ship provider or Syntarus API keys inside the APK.
- Prefer Android intents and official app APIs over accessibility automation.
- Ask for a permission only when the user invokes the related capability.
- Keep cloud history and local history scoped to the signed-in account.
- Never execute payments, messages, deletion, or account changes without a final human confirmation.
