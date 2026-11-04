"""Web search for the agent.

Runs in the same browser the agent browses with: the owner's own PC when their bridge is online (their IP,
so search engines do not treat it as a datacenter bot), otherwise the server's browser. Read-only, so it
runs without a tap; it returns a short list the agent can then open with browser_step.
"""
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

from src.agent.policy import SessionCtx

ENGINES = (
    ("duckduckgo", "https://html.duckduckgo.com/html/?q={q}"),
    ("bing", "https://www.bing.com/search?q={q}"),
)
SKIP_HOSTS = ("duckduckgo.com", "bing.com", "microsoft.com", "msn.com", "go.microsoft.com", "r.bing.com")
MAX_RESULTS = 8


def real_url(href: str) -> str:
    """Search engines wrap result links in redirects; return the destination."""
    u = urlparse(href if "://" in href else "https:" + href if href.startswith("//") else href)
    q = parse_qs(u.query)
    for key in ("uddg", "u", "url"):
        if key in q and q[key]:
            target = unquote(q[key][0])
            if target.startswith("http"):
                return target
    return href


def parse_results(elements: list[dict]) -> list[dict]:
    """Group links by destination: the first text is the title, a longer later one is the snippet."""
    seen: dict[str, dict] = {}
    for e in elements:
        if e.get("tag") != "a" or not e.get("href"):
            continue
        url = real_url(e["href"])
        host = (urlparse(url).hostname or "").lower()
        if not url.startswith("http") or any(host == h or host.endswith("." + h) for h in SKIP_HOSTS):
            continue
        text = " ".join((e.get("text") or "").split())
        if not text:
            continue
        hit = seen.setdefault(url, {"title": text, "url": url, "snippet": ""})
        if text != hit["title"] and len(text) > len(hit["snippet"]):
            hit["snippet"] = text[:300]
    return list(seen.values())[:MAX_RESULTS]


async def web_search(app, ctx: SessionCtx, args: dict) -> dict:
    query = " ".join(str(args.get("query", "")).split())[:300]
    if len(query) < 2:
        return {"error": "query is too short"}
    browsers, sid, last_error = app.state.browsers, f"{ctx.id}:search", ""
    try:
        for name, template in ENGINES:
            out = await browsers.step(sid, {"action": "navigate", "url": template.format(q=quote_plus(query))},
                                      tenant_id=ctx.tenant_id)
            if out.get("error"):
                last_error = out["error"]
                await browsers.close(sid)
                continue
            results = parse_results(out.get("elements") or [])
            if results:
                via = browsers.where(sid) if hasattr(browsers, "where") else ""
                return {"query": query, "engine": name, "via": via or "server", "results": results,
                        "next": "open a result with browser_step navigate, then read it"}
            last_error = f"{name} returned no results (possibly a bot check)"
            await browsers.close(sid)
    finally:
        await browsers.close(sid)
    return {"error": f"search did not work: {last_error[:200]}. If this keeps happening, ask the owner to send 'pair browser' "
                     "so searches run in their own browser."}
