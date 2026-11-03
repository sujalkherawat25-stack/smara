"""`python -m src.box.worker <tool> <json-args>` -> one JSON line on stdout.

Playwright needs the Proactor event loop and psycopg needs the Selector loop on
Windows, so browser work runs in its own process (which also isolates Chromium).
"""
import asyncio
import json
import sys

from src.box.tools import run_personal_browser_task, scrape_indiamart_quotes

TOOLS = {
    "scrape_indiamart_quotes": lambda a: scrape_indiamart_quotes(a["product"], a.get("limit", 10)),
    "run_personal_browser_task": lambda a: run_personal_browser_task(a["plan"]),
}


async def main() -> dict:
    tool, args = sys.argv[1], json.loads(sys.argv[2])
    if tool not in TOOLS:
        return {"error": f"unknown tool {tool}"}
    try:
        return await TOOLS[tool](args)
    except Exception as e:
        return {"error": f"{type(e).__name__}: {str(e)[:300]}"}


if __name__ == "__main__":
    print(json.dumps(asyncio.run(main())))
