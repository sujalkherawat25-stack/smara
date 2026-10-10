# Smara v0.1.10 — Safety and setup hardening

This update tightens the native-first release boundary and makes first-run
configuration easier to verify.

- Adds a Help & Setup area with explicit, user-confirmed model and web-search
  connectivity checks. The probes use fixed minimal inputs, protected local
  credentials, and return status/latency rather than response bodies.
- Replaces the legacy calculator's Python evaluator with bounded arithmetic
  parsing. Dynamic Python compatibility paths are disabled by default and
  excluded from the wheel, Desktop bundle, and portable CLI bundle.
- Sanitizes Mermaid SVG before display or download, including external
  references, scripting/event attributes, and SVG animation elements.
- Adds separate CI lanes for native contracts, legacy compatibility,
  browser/worker isolation, deterministic research quality, frontends, and
  distributable boundaries.

The browser remains opt-in to explicit public origins. Isolated desktop
execution is limited to the disposable Docker guest where available; native
Windows desktop control and unattended company-write automation are not
enabled. Search/model probes are not run automatically and may incur provider
charges only after a user confirms them.
