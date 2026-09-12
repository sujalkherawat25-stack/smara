"""Bounded, credential-safe provider smoke for the canonical Windows agent."""
from __future__ import annotations

import getpass
import json
import tempfile
import argparse
from pathlib import Path

from smara.autonomous_agent import SmaraAutonomousAgent
from smara.harness import Budget, SessionEngine


BASE_URL = "https://api.sarvam.ai/v2"
MODEL = "glm5.3-flash"


def agent(root: Path, session_id: str, key: str, profile: str, calls: int) -> tuple[SmaraAutonomousAgent, SessionEngine]:
    session = SessionEngine(root, session_id, budget=Budget(240, 20, calls, 180_000, 2), constrained=False)
    return SmaraAutonomousAgent(
        api_key=key,
        base_url=BASE_URL,
        model=MODEL,
        auth_header="api-subscription-key",
        workspace_root=root,
        profile=profile,
        session_engine=session,
        max_iterations=calls,
    ), session


def summary(case: str, result: dict, validated: bool) -> dict:
    return {
        "case": case,
        "validated": validated,
        "status": result.get("status"),
        "completed": result.get("completed"),
        "iterations": result.get("iterations"),
        "usage": result.get("session", {}).get("usage"),
        "unresolved": result.get("session", {}).get("unresolved_items", []),
    }


def main() -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument("--case",choices=["all","research","local","browser"],default="all")
    args=parser.parse_args()
    key = getpass.getpass("Temporary Sarvam API key: ").strip()
    if not key:
        raise SystemExit("A temporary Sarvam API key is required.")
    outcomes: list[dict] = []

    if args.case in {"all","research"}:
      research_root = Path(tempfile.mkdtemp(prefix="smara-research-smoke-"))
      (research_root / "evidence.txt").write_text("Mass is 42 kilograms.", encoding="utf-8")
      research, _ = agent(research_root, "research-claim", key, "research", 8)
      research_result = research.run(
        "Use canonical research tools. Plan one node for the claim Mass is 99 kilograms. "
        "Ingest evidence.txt. Resolve the original claim with that evidence ID. Then validate "
        "the supported evidence statement exactly: Mass is 42 kilograms. using the same evidence "
        "ID. Finish with the validated statement and FINAL LABEL: refuted. Do not browse.",
        max_iterations=8,
    )
      outcomes.append(summary("research-claim", research_result, research_result.get("completed") is True and "refuted" in research_result.get("answer", "").lower()))

    if args.case in {"all","local"}:
      local_root = Path(tempfile.mkdtemp(prefix="smara-local-smoke-"))
      local, _ = agent(local_root, "local-json", key, "coding", 7)
      local.session_engine.set("output_contract",{"artifacts":[{"path":"answer.json","kind":"json","checks":{"expected":{"value":42}}}]})
      local_result = local.run(
        "Create answer.json containing exactly the JSON object with key value and integer 42. "
        "Use file_write, verify it with file_read, and finish. Do not install packages.",
        max_iterations=7,
      )
      local_ok = (local_root / "answer.json").is_file() and json.loads((local_root / "answer.json").read_text(encoding="utf-8")) == {"value": 42}
      outcomes.append(summary("local-json", local_result, local_ok))

    if args.case in {"all","browser"}:
      browser_root = Path(tempfile.mkdtemp(prefix="smara-browser-smoke-"))
      (browser_root / "fixture.html").write_text(
        "<form onsubmit=\"const v=document.querySelector('input').value;document.body.dataset.saved=v;"
        "document.querySelector('p').textContent='Saved: '+v;return false\">"
        "<input><button>Save</button></form><p>Not saved</p>",
        encoding="utf-8",
    )
      browser, _ = agent(browser_root, "browser-form", key, "full", 8)
      browser_result = browser.run(
        f"Open {browser_root.joinpath('fixture.html').as_uri()} with browser_open. Fill the input "
        "with saved and click Save using fresh DOM references. Observe and finish only after "
        "confirming the form state changed.",
        max_iterations=8,
    )
      browser_id = browser._browser.browser_session_id
      browser_ok = bool(
        browser_id
        and browser._browser.backend.page(browser._browser.backend.sessions[browser_id]).locator("body").get_attribute("data-saved") == "saved"
    )
      outcomes.append(summary("browser-form", browser_result, browser_ok))
      browser._browser.shutdown()

    print(json.dumps({"provider": "sarvam", "model": MODEL, "outcomes": outcomes}, ensure_ascii=False))
    return 0 if all(item["validated"] and item["completed"] for item in outcomes) else 1


if __name__ == "__main__":
    raise SystemExit(main())
