# W4 Windows setup and discovery gate

Date: 2026-09-09.

`smara doctor --json` now reports configured, available, tested and unavailable
independently for workspace writes, session/artifact persistence, durable process
operations, model and search providers, PDF/OCR extraction and the installed
browser backend. It performs isolated functional probes and prints no secrets. It
also publishes explicit research and local-execution profiles with capabilities,
context accounting, budgets and output contracts.

The application envelope and authenticated local transport expose the same
session ID and event stream. The application response additionally carries status,
remaining budget, artifact locations, unresolved work and a resume instruction.

Focused source test: **7 passed in 2.95s**. Wheel
`dist/smara-0.1.0-py3-none-any.whl` has SHA-256
`bf400683db637b0c04d6e512e9a541a581007acc20aede34e2bbf2a01fa68884`.
It was installed with declared dependencies and the browser extra into
`%TEMP%\smara-w4-clean-20260909\venv`, then exercised from the external Unicode
and space-containing workspace `%TEMP%\smara-w4-clean-20260909\workspace ü space`
without `PYTHONPATH`. Installed doctor, browser, file, terminal and conservative
research-claim smokes passed. The imported package path was the clean venv's
`Lib\site-packages\smara`, not this source checkout.

Model and live search credentials were not configured, so doctor correctly marks
those provider checks unavailable rather than tested. OCR is also unavailable.
Linux and disposable-VM desktop remain explicitly deferred by the active plan.

Final source regression: **543 passed, 1 skipped, 2 existing JWT-key warnings in
147.97 seconds**.
