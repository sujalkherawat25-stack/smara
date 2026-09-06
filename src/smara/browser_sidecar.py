"""Browser Automation & Web Research Sidecar."""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import time
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass
class BrowserStepResult:
    step_index: int
    action: str  # "navigate" | "assert_title" | "assert_text" | "screenshot" | "scrape" | "click"
    target: str
    status: str  # "passed" | "failed" | "info"
    duration_ms: int
    details: str
    screenshot_base64: str | None = None
    dom_snapshot: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class E2ESuiteResult:
    suite_name: str
    success: bool
    passed_count: int
    failed_count: int
    total_duration_ms: int
    steps: list[BrowserStepResult]
    failure_reason: str | None = None
    suggested_fix: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["steps"] = [s.to_dict() if isinstance(s, BrowserStepResult) else s for s in self.steps]
        return d


class BrowserSidecarEngine:
    """Headless browser engine for scraping, real screenshot capture, and E2E UI testing."""

    def __init__(self, workspace_root: Path | None = None):
        self.workspace = (workspace_root or Path.cwd()).resolve()
        self.snapshots_dir = self.workspace / ".smara" / "browser_snapshots"
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)
        self.browser_bin = self._discover_browser()

    def _discover_browser(self) -> str | None:
        """Finds native Edge or Chrome headless executable on Windows/OS."""
        candidates = [
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe"),
            os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
            "msedge",
            "google-chrome",
            "chromium",
        ]
        for c in candidates:
            if Path(c).is_file():
                return c
        return None

    def capture_screenshot(self, url: str, output_path: Path | None = None) -> dict[str, Any]:
        """Captures a real PNG screenshot of a page and returns base64 PNG data URL."""
        if not url.startswith(("http://", "https://", "file://")):
            url = "https://" + url

        target_file = output_path or (self.snapshots_dir / f"screen_{int(time.time() * 1000)}.png")

        if self.browser_bin:
            try:
                cmd = [
                    self.browser_bin,
                    "--headless",
                    "--disable-gpu",
                    f"--screenshot={target_file}",
                    "--window-size=1280,800",
                    "--hide-scrollbars",
                    url,
                ]
                res = subprocess.run(cmd, capture_output=True, timeout=15)
                if target_file.exists() and target_file.stat().st_size > 0:
                    raw_bytes = target_file.read_bytes()
                    b64 = base64.b64encode(raw_bytes).decode("ascii")
                    return {
                        "ok": True,
                        "success": True,
                        "url": url,
                        "file_path": str(target_file),
                        "file_size": len(raw_bytes),
                        "data_url": f"data:image/png;base64,{b64}",
                    }
            except Exception as e:
                pass

        # A diagnostic illustration is not an observation.  In particular,
        # never claim a capture exists when no browser backend produced PNG
        # bytes at the requested target.
        return {
            "ok": False,
            "success": False,
            "url": url,
            "file_path": None,
            "file_size": 0,
            "data_url": "data:application/x-smara-unavailable;base64,",
            "error_kind": "screenshot_unavailable",
            "error": "No browser backend produced a screenshot.",
        }

    def scrape_url(self, url: str) -> dict[str, Any]:
        """Scrapes DOM, title, meta descriptions, and clean text."""
        if not url.startswith(("http://", "https://", "file://")):
            url = "https://" + url

        start = time.time()
        dom = ""
        # Try headless browser dump-dom for client-rendered JS pages
        if self.browser_bin:
            try:
                cmd = [self.browser_bin, "--headless", "--disable-gpu", "--dump-dom", url]
                res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=12)
                if res.returncode == 0 and res.stdout.strip():
                    dom = res.stdout.strip()
            except Exception:
                pass

        # Fallback to standard HTTP GET if browser dump didn't run
        if not dom:
            try:
                req = urllib.request.Request(
                    url,
                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Smara/2.0"},
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    dom = resp.read().decode("utf-8", errors="replace")
            except Exception as e:
                return {
                    "success": False,
                    "url": url,
                    "error": str(e),
                    "duration_ms": int((time.time() - start) * 1000),
                }

        # Extract title
        title_match = re.search(r"<title>(.*?)</title>", dom, re.IGNORECASE | re.DOTALL)
        title = title_match.group(1).strip() if title_match else "Untitled"

        # Extract headings
        headings = [h.strip() for h in re.findall(r"<h[1-3][^>]*>(.*?)</h[1-3]>", dom, re.IGNORECASE | re.DOTALL)[:8]]
        clean_headings = [re.sub(r"<[^>]+>", "", h) for h in headings]

        # Extract clean text
        cleaned_text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", dom, flags=re.IGNORECASE | re.DOTALL)
        cleaned_text = re.sub(r"<[^>]+>", " ", cleaned_text)
        cleaned_text = " ".join(cleaned_text.split())

        duration_ms = int((time.time() - start) * 1000)
        return {
            "success": True,
            "url": url,
            "title": title,
            "headings": clean_headings,
            "content_snippet": cleaned_text[:1500],
            "dom_length": len(dom),
            "duration_ms": duration_ms,
        }

    def run_e2e_flow(self, suite_name: str, steps: list[dict[str, Any]]) -> E2ESuiteResult:
        """Executes a series of E2E browser test actions and records visual replay steps."""
        step_results: list[BrowserStepResult] = []
        overall_start = time.time()
        current_dom = ""
        current_title = ""
        current_url = ""

        for idx, step in enumerate(steps, 1):
            action = step.get("action", "navigate")
            target = step.get("target", "")
            expected = step.get("expected", "")
            t0 = time.time()

            try:
                if action == "navigate":
                    current_url = target
                    scrape_data = self.scrape_url(current_url)
                    dt = int((time.time() - t0) * 1000)
                    if scrape_data["success"]:
                        current_dom = scrape_data.get("content_snippet", "")
                        current_title = scrape_data.get("title", "")
                        step_results.append(BrowserStepResult(
                            step_index=idx,
                            action="navigate",
                            target=current_url,
                            status="passed",
                            duration_ms=dt,
                            details=f"Loaded page: '{current_title}' (DOM length {scrape_data.get('dom_length', 0)})",
                            dom_snapshot=scrape_data.get("content_snippet", "")[:300],
                        ))
                    else:
                        step_results.append(BrowserStepResult(
                            step_index=idx,
                            action="navigate",
                            target=current_url,
                            status="failed",
                            duration_ms=dt,
                            details=f"Failed to navigate: {scrape_data.get('error', 'Unknown error')}",
                        ))
                        break

                elif action == "assert_title":
                    dt = int((time.time() - t0) * 1000)
                    if expected.lower() in current_title.lower():
                        step_results.append(BrowserStepResult(
                            step_index=idx,
                            action="assert_title",
                            target=expected,
                            status="passed",
                            duration_ms=dt,
                            details=f"Title correctly matches expected '{expected}' (actual: '{current_title}')",
                        ))
                    else:
                        step_results.append(BrowserStepResult(
                            step_index=idx,
                            action="assert_title",
                            target=expected,
                            status="failed",
                            duration_ms=dt,
                            details=f"Title mismatch! Expected '{expected}', but actual was '{current_title}'",
                        ))
                        break

                elif action == "assert_text":
                    dt = int((time.time() - t0) * 1000)
                    if expected.lower() in current_dom.lower():
                        step_results.append(BrowserStepResult(
                            step_index=idx,
                            action="assert_text",
                            target=expected,
                            status="passed",
                            duration_ms=dt,
                            details=f"Text '{expected}' found in DOM snapshot",
                        ))
                    else:
                        step_results.append(BrowserStepResult(
                            step_index=idx,
                            action="assert_text",
                            target=expected,
                            status="failed",
                            duration_ms=dt,
                            details=f"Assertion failed: Text '{expected}' was not present in page DOM",
                        ))
                        break

                elif action == "screenshot":
                    shot = self.capture_screenshot(current_url)
                    dt = int((time.time() - t0) * 1000)
                    step_results.append(BrowserStepResult(
                        step_index=idx,
                        action="screenshot",
                        target=current_url,
                        status="passed" if shot.get("success") else "failed",
                        duration_ms=dt,
                        details=("High-res screenshot captured for visual replay" if shot.get("success") else shot.get("error", "Screenshot unavailable")),
                        screenshot_base64=shot.get("data_url"),
                    ))
                    if not shot.get("success"):
                        break

                else:
                    dt = int((time.time() - t0) * 1000)
                    step_results.append(BrowserStepResult(
                        step_index=idx,
                        action=action,
                        target=target,
                        status="failed",
                        duration_ms=dt,
                        details=f"Unsupported browser action '{action}' was not executed.",
                    ))
                    break

            except Exception as e:
                dt = int((time.time() - t0) * 1000)
                step_results.append(BrowserStepResult(
                    step_index=idx,
                    action=action,
                    target=target,
                    status="failed",
                    duration_ms=dt,
                    details=f"Exception during step execution: {e}",
                ))
                break

        passed_count = sum(1 for s in step_results if s.status == "passed")
        failed_count = sum(1 for s in step_results if s.status == "failed")
        success = failed_count == 0 and len(step_results) == len(steps)
        total_ms = int((time.time() - overall_start) * 1000)

        failure_reason = None
        suggested_fix = None
        if not success:
            failed_step = next((s for s in step_results if s.status == "failed"), None)
            if failed_step:
                failure_reason = failed_step.details
                suggested_fix = f"Verify component renders expected '{failed_step.target}' in DOM or update locator/selector."

        return E2ESuiteResult(
            suite_name=suite_name,
            success=success,
            passed_count=passed_count,
            failed_count=failed_count,
            total_duration_ms=total_ms,
            steps=step_results,
            failure_reason=failure_reason,
            suggested_fix=suggested_fix,
        )

    def diagnose_and_heal_component(self, broken_text: str) -> dict[str, Any]:
        """Scans workspace UI files and proposes auto-fix for missing/broken text."""
        candidates = []
        for root, dirs, files in os.walk(self.workspace / "apps" / "desktop" / "src"):
            for file in files:
                if file.endswith((".tsx", ".jsx", ".ts", ".html")):
                    p = Path(root) / file
                    try:
                        content = p.read_text(encoding="utf-8")
                        if broken_text.lower() in content.lower():
                            candidates.append(str(p.relative_to(self.workspace)))
                    except Exception:
                        pass

        return {
            "query": broken_text,
            "matching_files": candidates,
            "status": "ready_to_heal" if candidates else "not_found",
            "recommendation": f"Found {len(candidates)} component files containing references to '{broken_text}'.",
        }

    scrape_dom = scrape_url
    run_e2e_suite = run_e2e_flow
    diagnose_component_failure = diagnose_and_heal_component
