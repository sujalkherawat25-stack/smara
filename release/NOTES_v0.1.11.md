# Smara v0.1.11 — portable sandbox and browser setup

This patch follows the v0.1.10 safety and setup hardening with two practical
runtime fixes and stronger fresh-runner checks.

- POSIX workspace sandboxes run as the workspace's non-root owner so permitted
  writes work across Linux distributions with different user IDs. The sandbox
  still has a read-only root filesystem, no network, dropped capabilities,
  resource limits, and only the approved workspace mounted.
- Managed browser discovery now uses Playwright's installed Chromium when no
  system Chrome or Edge is present.
- CI provisions that browser and the isolated coding images before running the
  browser, worker, and Docker boundary tests.
- The pytest bootstrap now creates its temp parent on a clean checkout.

The app remains opt-in for browser/research readers. Desktop settings and
protected credentials are preserved by the local installer.
