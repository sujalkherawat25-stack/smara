"""Skills: short instruction packs the agent loads only when a task needs them (keeps the main prompt small).

Each skill is skills/<name>/SKILL.md with `name` and `description` frontmatter. The prompt lists name + description;
the load_skill tool returns the body.
"""
import os
import re
from functools import lru_cache
from pathlib import Path

MAX_BODY = 12_000


def skills_dir() -> Path:
    env = os.environ.get("SKILLS_DIR")
    for p in ([Path(env)] if env else []) + [Path.cwd() / "skills", Path(__file__).resolve().parents[2] / "skills"]:
        if p.is_dir():
            return p
    return Path("skills")


def parse(text: str) -> tuple[dict, str]:
    m = re.match(r"---\s*\n(.*?)\n---\s*\n?(.*)", text, re.S)
    if not m:
        return {}, text
    meta = {}
    for line in m.group(1).splitlines():
        k, _, v = line.partition(":")
        if v.strip():
            meta[k.strip()] = v.strip()
    return meta, m.group(2).strip()


@lru_cache(maxsize=4)
def _load(root: str) -> dict[str, dict]:
    out = {}
    for f in sorted(Path(root).glob("*/SKILL.md")):
        meta, body = parse(f.read_text(encoding="utf-8"))
        name = meta.get("name") or f.parent.name
        if re.fullmatch(r"[a-z0-9-]{1,60}", name) and meta.get("description"):
            out[name] = {"description": meta["description"], "body": body[:MAX_BODY]}
    return out


def all_skills() -> dict[str, dict]:
    return _load(str(skills_dir()))


def index() -> str:
    """Lines for the system prompt."""
    return "\n".join(f"- {n}: {s['description']}" for n, s in all_skills().items())


async def load_skill(app, ctx, args: dict) -> dict:
    name = str(args.get("name", "")).strip()
    skill = all_skills().get(name)
    if not skill:
        return {"error": f"no skill named {name!r}", "available": sorted(all_skills())}
    return {"name": name, "instructions": skill["body"]}
