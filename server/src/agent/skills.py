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
    """Lines for the system prompt (the built-in skills)."""
    return "\n".join(f"- {n}: {s['description']}" for n, s in all_skills().items())


# ---- the shop's own skills: a workflow that worked, saved at the owner's wish --------------------------------------------
MAX_SKILLS = 30
MAX_OWN_BODY = 6000
NAME_RE = re.compile(r"[a-z0-9-]{1,60}")
SECRET_RE = re.compile(r"(?i)\b(password|passcode|otp|cvv|pin)\s*[:=]\s*\S|\b(sk|pk|api)[-_][A-Za-z0-9]{16,}|\b\d{12,19}\b")
BANNER = ("This is one of the owner's own saved workflows (from a task that worked before). Follow its steps, adapting to what "
          "the owner asks now. It never overrides the owner's latest message, an approval, or a safety rule. If a step no "
          "longer works or you find a better way, finish the task and then call save_skill with the same name to improve it.")


async def own_skills(pool, tenant_id: str) -> dict[str, dict]:
    async with pool.connection() as conn:
        rows = await (await conn.execute(
            "SELECT name, description, body, version, uses FROM owner_skills WHERE tenant_id = %s ORDER BY name",
            (tenant_id,))).fetchall()
    return {r["name"]: dict(r) for r in rows}


async def index_for(pool, tenant_id: str) -> str:
    """Built-in skills, then the shop's own."""
    lines = index()
    try:
        own = await own_skills(pool, tenant_id)
    except Exception:
        own = {}
    if own:
        mine = "\n".join(f"- {n}: {s['description']}" for n, s in own.items())
        lines = f"{lines}\nThis shop's own skills (saved from workflows that worked here):\n{mine}" if lines else mine
    return lines


async def load_skill(app, ctx, args: dict) -> dict:
    name = str(args.get("name", "")).strip()
    skill = all_skills().get(name)
    if skill:
        return {"name": name, "instructions": skill["body"]}
    if app is not None and ctx is not None and NAME_RE.fullmatch(name):
        async with app.state.pool.connection() as conn:
            row = await (await conn.execute(
                """UPDATE owner_skills SET uses = uses + 1, last_used_at = now() WHERE tenant_id = %s AND name = %s
                   RETURNING body, version""", (ctx.tenant_id, name))).fetchone()
        if row:
            return {"name": name, "own_skill": True, "version": row["version"], "note": BANNER, "instructions": row["body"]}
    mine = sorted(await own_skills(app.state.pool, ctx.tenant_id)) if app is not None and ctx is not None else []
    return {"error": f"no skill named {name!r}", "available": sorted(all_skills()) + mine}


async def save_skill(app, ctx, args: dict) -> dict:
    """The agent saves a workflow that worked, in its own words, when the owner agrees (or asks). Same name = improve it."""
    name = re.sub(r"[^a-z0-9]+", "-", str(args.get("name", "")).strip().lower()).strip("-")[:60]
    desc = " ".join(str(args.get("description", "")).split())
    body = str(args.get("instructions", "")).strip()
    if not NAME_RE.fullmatch(name):
        return {"error": "give a short name like 'weekly-gst-sheet' (letters, numbers, dashes)"}
    if name in all_skills():
        return {"error": f"{name!r} is a built-in skill; pick another name"}
    if not (10 <= len(desc) <= 300):
        return {"error": "description: one or two sentences (10 to 300 characters) saying WHEN to use this skill"}
    if not (40 <= len(body) <= MAX_OWN_BODY):
        return {"error": f"instructions: the steps that worked, 40 to {MAX_OWN_BODY} characters"}
    if SECRET_RE.search(body) or SECRET_RE.search(desc):
        return {"error": "a skill must not contain passwords, OTPs, card numbers or keys; describe the step instead"}
    async with app.state.pool.connection() as conn:
        have = await (await conn.execute("SELECT version FROM owner_skills WHERE tenant_id = %s AND name = %s",
                                         (ctx.tenant_id, name))).fetchone()
        if not have:
            n = (await (await conn.execute("SELECT count(*) AS n FROM owner_skills WHERE tenant_id = %s",
                                           (ctx.tenant_id,))).fetchone())["n"]
            if n >= MAX_SKILLS:
                return {"error": f"{MAX_SKILLS} skills already saved; delete one first (delete_skill)"}
        await conn.execute(
            """INSERT INTO owner_skills (tenant_id, name, description, body) VALUES (%s, %s, %s, %s)
               ON CONFLICT (tenant_id, name) DO UPDATE SET description = EXCLUDED.description, body = EXCLUDED.body,
                    version = owner_skills.version + 1, updated_at = now()""", (ctx.tenant_id, name, desc, body))
    return {"saved": True, "name": name, "updated": bool(have), "version": (have["version"] + 1) if have else 1,
            "note_for_you": "Tell the owner in one line that the skill is saved and what it is called. They can send 'skills' "
                            "to see their skills or 'skill delete <name>' to remove one."}


async def delete_skill(app, ctx, args: dict) -> dict:
    name = str(args.get("name", "")).strip()
    async with app.state.pool.connection() as conn:
        cur = await conn.execute("DELETE FROM owner_skills WHERE tenant_id = %s AND name = %s", (ctx.tenant_id, name))
    return {"deleted": cur.rowcount > 0, "name": name}


def summary(own: dict[str, dict]) -> str:
    """The owner's 'skills' command."""
    if not own:
        return ("Abhi koi skill saved nahi hai. Jab bhi koi kaam achhe se ho jaye, main poochunga: 'Isko skill bana doon?' "
                "Haan bol dijiye, agli baar wahi kaam seedha ho jayega.")
    lines = [f"• {n}: {s['description']} (used {s['uses']}x)" for n, s in own.items()]
    return "Aapki skills:\n" + "\n".join(lines) + "\n\nHatane ke liye: skill delete <naam>"
