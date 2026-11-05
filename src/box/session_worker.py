"""Long-lived browser for one agent session: one JSON command per stdin line,
one JSON result per stdout line. The page persists between steps, so the agent
can observe -> act -> observe. Same SSRF guard as the one-shot tools.

It runs the very same engine as the owner's PC helper (bridge/munim_bridge.py):
numbered refs for every control, actions by ref, and a report of what each step
changed. One way of seeing and acting, wherever the browser is.

    {"action": "navigate", "url": "https://..."} | {"action": "read"} | {"action": "text"}
    {"action": "click", "ref": 12} | {"action": "fill", "ref": 7, "value": "..."} | {"action": "press", "value": "Enter"}
"""
import asyncio
import json
import sys
from pathlib import Path

from src.box.guard import check_url
from src.box.tools import browser_page

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bridge"))
import munim_bridge as engine  # noqa: E402  (the shared browser engine)


async def handle(page, cmd: dict) -> dict:
    if cmd.get("action") == "navigate":
        await check_url(str(cmd.get("url", "")))          # resolves the host: nothing private, ever
    return await engine.handle(page, cmd, allow_private=False)


async def main() -> None:
    loop = asyncio.get_running_loop()
    async with browser_page() as page:
        print(json.dumps({"ready": True}), flush=True)
        while line := await loop.run_in_executor(None, sys.stdin.readline):
            try:
                out = await handle(page, json.loads(line))
            except Exception as e:
                out = {"error": f"{type(e).__name__}: {str(e)[:300]}"}
            print(json.dumps(out, default=str), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
