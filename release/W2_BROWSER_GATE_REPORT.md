# W2 canonical browser gate

Date: 2026-09-09. Scope: deterministic Windows/local-Chromium browser integration.

The canonical autonomous loop now exposes typed browser open, observe, navigate,
act, tabs, switch, scroll, download and close calls through its session capability
boundary. A browser context has one stable owner and session, observations and
screenshots are durable artifacts, element references are observation-bound, paths
and URLs are checked at the broker boundary, cancellation closes owned work, and a
lost backend handle is invalidated rather than silently reused.

The five required agent-loop journeys each passed three times from fresh contexts:
search/open/cite, multi-tab comparison, form submit with independent page-state
inspection, download followed by local-file analysis, and explicit recovery after
backend loss followed by session cancellation. The source fixture is
`tests/test_w2_browser_agent_integration.py`; the 15-run inventory is
`tests/evals/windows_browser/manifest.json`.

Commands (from the repository root with `PYTHONPATH=src`):

```text
.venv\Scripts\python.exe -m pytest -q tests/test_w2_browser_agent_integration.py
# 15 passed in 31.05s
```

This gate proves local fixture workflows, not arbitrary live-site compatibility,
authenticated user profiles, host desktop control, or disposable-VM execution.
Those remain outside W2. W3-W5 are still required for Windows autonomy readiness.
