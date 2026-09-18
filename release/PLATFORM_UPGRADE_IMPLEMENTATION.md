# Smara Platform Upgrade — Implementation Report

Date: 2026-09-18

This release implements the five requested platform seams without replacing
the existing Desktop/CLI behavior:

1. **Unified runtime/session contract**
   - `src/smara/runtime_session.py` adds a versioned SQLite session envelope
     and append-only lifecycle event ledger.
   - Local CLI/Desktop turns and hosted API turns now share the same status,
     result, unresolved-work, profile, revision, and resume semantics.
   - Hosted status is queryable at `GET /v1/runtime-sessions/{session_id}`.

2. **Skills and learned-skill promotion**
   - `SkillLifecycleManager` records candidate/quarantined/promoted/rejected/
     revoked states under `.smara/skills/.lifecycle.json`.
   - Promotion requires a passing reproducible gate, at least 90% overall, at
     least 80% per category, and zero false completions/safety violations.
   - `/learn` writes a tested candidate; it does not silently promote it.
   - CLI: `smara skills list|view|promote|revoke`.

3. **Remote MCP/OAuth and plugin lifecycle**
   - MCP now supports public HTTPS JSON-RPC HTTP transports, tool refresh and
     reload, PKCE authorization URL generation, and authorization-code token
     exchange. Private endpoints require explicit `allow_private`.
   - Declarative plugins can be discovered and enabled/disabled/removed without
     importing arbitrary Python. CLI: `smara plugins list|enable|disable|remove`.

4. **WSL/Linux and isolated execution**
   - Docker execution retains network isolation, read-only root, dropped
     capabilities, no-new-privileges, memory/CPU/PID ceilings.
   - WSL2 command construction, bounded execution, and availability probing are
     available through `smara backends`.
   - Native Windows VM/desktop control remains explicitly unclaimed when no
     hypervisor transport is configured; no false promotion is made.

5. **Local gateway and scheduler**
   - `GatewayLedger` and `LocalGateway` provide durable at-least-once inbound
     delivery with idempotency and retry state.
   - `WebhookGatewayServer` provides authenticated loopback webhook ingress;
     HTTPS outbound webhook adapters can deliver channel messages.
   - `LocalScheduleStore` and `LocalScheduler` provide atomic claims,
     interval recovery, retry state, and a background tick loop.
   - CLI: `smara gateway status|receive|retry|serve` and
     `smara schedule list|add|run|pause|resume|remove`.

## Verification

- Focused upgrade tests: **29 passed**.
- Full regression suite: **910 passed, 1 skipped**.
- Compile check: `python -m compileall -q src/smara` passed.

The installed Desktop package is not silently claimed as updated by this
source change. If the packaged executor is to include the new modules, run the
existing signed Desktop build/reinstall flow after review; the source CLI and
server use the new implementation immediately.
