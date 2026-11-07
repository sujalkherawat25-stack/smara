"""Safety rails for the headless browser: SSRF blocking and plan validation."""
import asyncio
import ipaddress
from urllib.parse import urlparse

ALLOWED_ACTIONS = {"goto", "click", "fill", "press", "extract_text", "wait"}
MUTATING_ACTIONS = {"click", "fill", "press"}
MAX_STEPS = 15


def _ip_blocked(ip: str) -> bool:
    a = ipaddress.ip_address(ip)
    return not a.is_global or a.is_multicast


def host_blocked(host: str) -> bool:
    """Literal check (no DNS): localhost, internal suffixes, private/metadata IPs."""
    host = (host or "").strip("[]").lower()
    if not host or host == "localhost" or host.endswith((".local", ".internal", ".localhost")):
        return True
    try:
        return _ip_blocked(host)
    except ValueError:
        return False


async def check_url(url: str) -> None:
    """Raise ValueError unless url is http(s) and resolves only to public addresses."""
    u = urlparse(url)
    if u.scheme not in ("http", "https") or host_blocked(u.hostname or ""):
        raise ValueError(f"blocked url: {url}")
    infos = await asyncio.get_running_loop().getaddrinfo(u.hostname, u.port or 443)
    if any(_ip_blocked(i[4][0]) for i in infos):
        raise ValueError(f"blocked url (resolves to a non-public address): {url}")


def validate_plan(plan) -> list[dict]:
    if not isinstance(plan, list) or not plan or len(plan) > MAX_STEPS:
        raise ValueError(f"plan must be 1-{MAX_STEPS} steps")
    for i, step in enumerate(plan):
        if not isinstance(step, dict) or step.get("action") not in ALLOWED_ACTIONS:
            raise ValueError(f"step {i + 1}: unsupported action")
        if step["action"] == "goto" and urlparse(str(step.get("url", ""))).scheme not in ("http", "https"):
            raise ValueError(f"step {i + 1}: goto needs an http(s) url")
        if step["action"] in ("click", "fill", "extract_text") and not step.get("selector"):
            raise ValueError(f"step {i + 1}: {step['action']} needs a selector")
    if plan[0]["action"] != "goto":
        raise ValueError("plan must start with goto")
    return plan


def is_mutating(plan: list[dict]) -> bool:
    return any(s["action"] in MUTATING_ACTIONS for s in plan)


def describe_plan(plan: list[dict]) -> str:
    out = []
    for i, s in enumerate(plan, 1):
        a = s["action"]
        detail = s.get("url") or s.get("selector") or s.get("value") or ""
        out.append(f"{i}. {a} {str(detail)[:60]}".strip())
    return "\n".join(out)
