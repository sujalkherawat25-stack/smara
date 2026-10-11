"""Explicitly approved synthetic live smoke checks, not full quality acceptance.

At most two inference requests (three with an explicitly approved credential
recheck) under a shared INR 50 conservative estimate,
plus one independently approved, one-image Document AI job. No private code,
user history, or remote memory. API keys and raw error bodies are not recorded.
"""
import argparse
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile

import httpx
from PIL import Image, ImageDraw, ImageFont

from smara.native_documents import DocumentError, describe_image, digitise, sarvam_key
from smara.native_profiles import load_profiles, resolve_profile_key
from smara.native_provider import ChatEndpoint, ResponsesAdapter
from scripts.run_native_live_workers_browser import BudgetClient


class ImageBudgetClient:
    def __init__(self, client, budget):
        self.client, self.budget = client, budget

    @asynccontextmanager
    async def stream(self, method, url, **kwargs):
        payload = kwargs["json"]
        payload["max_tokens"] = min(payload.get("max_tokens", 1024), 1024)
        reserve = (len(json.dumps(payload).encode()) + 1024 + payload["max_tokens"]) * 396 / 1_000_000
        with self.budget.lock:
            if self.budget.requests >= self.budget.ceiling or self.budget.reserved_rupees + reserve > 50:
                raise RuntimeError("Synthetic check ceiling reached before inference")
            self.budget.requests += 1
            self.budget.reserved_rupees += reserve
        async with self.client.stream(method, url, **kwargs) as response:
            yield response


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approved-live", action="store_true", required=True)
    parser.add_argument("--credential-recheck", type=Path, help="Approved single recheck after a rejected OCR submission; carries prior reservations forward")
    args = parser.parse_args()
    if not args.approved_live:
        return 2
    previous = None
    if args.credential_recheck:
        previous = json.loads(args.credential_recheck.read_text(encoding="utf-8"))
        if "HTTP 401" not in previous.get("ocr", {}).get("error", ""):
            raise SystemExit("Only an authentication-rejected OCR submission may be rechecked here")
        # One explicit recheck, never silently replay a previous accepted job.
        with (args.credential_recheck.parent / "credential-recheck-started.json").open("x", encoding="utf-8") as marker:
            marker.write('{"approved_recheck_started":true}')
    root = Path(__file__).resolve().parents[1]
    (root / "build").mkdir(exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="native-efficiency-live-", dir=root / "build"))
    image = Image.new("RGB", (900, 250), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 48)
    draw.text((25, 25), "SMARA SYNTHETIC TEST\nTotal: 391", font=font, fill="black")
    image.save(directory / "synthetic.png")
    profiles, _, credentials = load_profiles()
    profile = next(p for p in profiles if p.get("model") == "glm5.3" and str(p.get("base_url", "")).startswith("https://api.sarvam.ai"))
    endpoint = ChatEndpoint(profile["base_url"], profile["model"], resolve_profile_key(profile, credentials, allow_environment=False), profile.get("auth_header", "authorization"))
    report = {"checked_at": datetime.now(timezone.utc).isoformat(), "private_files_sent": False, "native_gui_tested": False, "full_research_quality_tested": False}
    with ResponsesAdapter(endpoint) as adapter:
        budget = BudgetClient(adapter.client, rupees=50, ceiling=3 if previous else 2, output_tokens=1024)
        if previous:
            budget.carry_forward(previous["inference_budget"])
        adapter.client = budget
        inputs = [{"role": "user", "content": "Synthetic research test: explain what can be concluded if this document could not be read. Do not invent its contents."}]
        for index in range(3):
            inputs.extend([{"type": "function_call", "name": "fetch_url", "call_id": f"synthetic-{index}", "arguments": '{"url":"https://example.com/synthetic.pdf"}'},
                {"type": "function_call_output", "call_id": f"synthetic-{index}", "output": "Unsupported source type. PDF text could not be retrieved. No evidence was read."}])
        request = {"stream": True, "max_output_tokens": 1024, "input": inputs,
            "client_metadata": {"thread_id": "synthetic-thread", "turn_id": "synthetic-turn"},
            "tools": [{"type": "function", "name": "fetch_url", "parameters": {"type": "object", "properties": {"url": {"type": "string"}}}}]}
        with httpx.Client(timeout=90, trust_env=False) as local:
            if previous:
                report["bounded_synthesis"] = previous["bounded_synthesis"]
                report["repeat_blocked_before_provider"] = previous["repeat_blocked_before_provider"]
                report["synthesis_evidence_retained_not_rerun"] = str(args.credential_recheck)
            else:
                check_synthesis(local, adapter, budget, request, report)
        async def images():
            key = sarvam_key()
            async with httpx.AsyncClient(timeout=30, trust_env=False, follow_redirects=False) as client:
                try:
                    result = await describe_image(directory, "synthetic.png", "Transcribe the visible text. What is the total?", client=ImageBudgetClient(client, budget), key=key)
                    report["image"] = {"status": "passed" if "391" in result["text"] else "failed", "text": result["text"], "model": result["model"]}
                except DocumentError as exc:
                    report["image"] = {"status": "unavailable", "error": str(exc)}
                try:
                    result = await digitise(directory, "synthetic.png", key=key)
                    report["ocr"] = {"status": "passed" if "391" in result["text"] else "failed", "text": result["text"], "job_status": result["status"]}
                except DocumentError as exc:
                    report["ocr"] = {"status": "unavailable", "error": str(exc)}
        asyncio.run(images())
        report["inference_budget"] = budget.summary()
        report["ocr_job_ceiling"] = 1
    (directory / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(directory / "report.json"), **report}, ensure_ascii=True))
    return 0 if (report["bounded_synthesis"]["completed"] and report["bounded_synthesis"]["tool_calls"] == 0
                 and report["repeat_blocked_before_provider"] and report["image"]["status"] == "passed"
                 and report["ocr"]["status"] == "passed") else 1


def check_synthesis(local, adapter, budget, request, report):
    response = local.post(adapter.base_url + "/responses", headers={"Authorization": "Bearer " + adapter.token}, json=request)
    events = [json.loads(line[5:]) for line in response.text.splitlines() if line.startswith("data:")]
    completed = next((event["response"] for event in events if event.get("type") == "response.completed"), None)
    report["bounded_synthesis"] = {"http_status": response.status_code, "completed": bool(completed),
        "tool_calls": sum(item.get("type") != "message" for item in (completed or {}).get("output", [])),
        "text": "\n".join(part.get("text", "") for item in (completed or {}).get("output", []) for part in item.get("content", []))}
    before = budget.requests
    blocked = local.post(adapter.base_url + "/responses", headers={"Authorization": "Bearer " + adapter.token}, json=request)
    report["repeat_blocked_before_provider"] = blocked.status_code == 400 and budget.requests == before


if __name__ == "__main__":
    raise SystemExit(main())
