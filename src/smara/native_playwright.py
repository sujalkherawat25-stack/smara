"""Smara Desktop chat's browser: Microsoft's Playwright MCP server, the browser tool commonly plugged into Codex.

It gives the model one plain tool per action (navigate, snapshot, click, type, press a key, select, hover, drag, tabs,
dialogs, wait) on an accessibility snapshot with refs. The version is pinned. Tools that run arbitrary page code
(browser_evaluate, browser_run_code_unsafe) are not offered; uploading a local file asks the owner first.
"""
from __future__ import annotations

from pathlib import Path

PACKAGE = "@playwright/mcp@0.0.84"
SERVER = "playwright"
TOOLS = ("browser_navigate", "browser_navigate_back", "browser_snapshot", "browser_click", "browser_type",
         "browser_fill_form", "browser_press_key", "browser_select_option", "browser_hover", "browser_drag",
         "browser_drop", "browser_wait_for", "browser_tabs", "browser_handle_dialog", "browser_find",
         "browser_take_screenshot", "browser_resize", "browser_close", "browser_file_upload", "browser_console_messages")
# Looking is free; anything that changes a page (click, type, keys, select, drag, upload) asks the owner first,
# as the Desktop always has.
ASK_FIRST = frozenset({"browser_click", "browser_type", "browser_fill_form", "browser_press_key", "browser_select_option",
                       "browser_drag", "browser_drop", "browser_file_upload"})
# Local and loopback addresses stay closed: the browser is for public websites.
PRIVATE = ";".join(f"{scheme}://{host}" for host in ("localhost", "127.0.0.1", "[::1]") for scheme in ("http", "https"))


def args(origins: set[str], any_site: str, workspace: Path, profile: Path, browser: str | None) -> list[str]:
    out = ["-y", PACKAGE, "--user-data-dir", str(profile), "--output-dir", str(workspace / ".smara" / "browser"),
           "--block-service-workers", "--blocked-origins", PRIVATE]
    if any_site not in origins:
        out += ["--allowed-origins", ";".join(sorted(origins))]
    if browser:
        out += ["--executable-path", browser]
    return out


def settings(npx: str, origins: set[str], any_site: str, workspace: Path, profile: Path, browser: str | None) -> dict:
    """Codex config entries for the Playwright MCP server."""
    key = f"mcp_servers.{SERVER}"
    out = {f"{key}.enabled": True, f"{key}.command": npx, f"{key}.args": args(origins, any_site, workspace, profile, browser),
           f"{key}.required": True, f"{key}.startup_timeout_sec": 120, f"{key}.tool_timeout_sec": 90,
           f"{key}.enabled_tools": list(TOOLS), f"{key}.default_tools_approval_mode": "prompt"}
    for name in TOOLS:
        out[f"{key}.tools.{name}.approval_mode"] = "prompt" if name in ASK_FIRST else "approve"
    return out
