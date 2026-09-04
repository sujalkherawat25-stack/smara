"""
Progressive Disclosure Skills System for Smara
Architecture:
- Tier 1 (skills_list): Token-efficient catalog with metadata (name, description, tags, version)
- Tier 2 (skill_view): Full SKILL.md markdown instructions loaded on demand
- Tier 3 (skill_view with relative_path): Supporting references, examples, and templates

Folder Layout:
  .smara/skills/
    my-skill/
      SKILL.md           # Required with YAML frontmatter
      references/        # Optional supporting documentation
      examples/          # Optional usage examples
      templates/         # Optional templates
"""

from __future__ import annotations
import json
import logging
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("smara.skills_system")


@dataclass
class SkillMetadata:
    name: str
    description: str
    version: str = "1.0.0"
    tags: List[str] = field(default_factory=list)
    platforms: List[str] = field(default_factory=list)
    source: str = "workspace"  # "workspace", "user", "builtin"
    skill_dir: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def parse_yaml_frontmatter(text: str) -> Tuple[Dict[str, Any], str]:
    """Parse YAML frontmatter enclosed by --- and return (metadata_dict, body_content)."""
    text = text.strip()
    if not text.startswith("---"):
        return {}, text

    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text

    frontmatter_raw = parts[1].strip()
    body = parts[2].strip()

    meta: Dict[str, Any] = {}
    for line in frontmatter_raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            key, val = line.split(":", 1)
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if val.startswith("[") and val.endswith("]"):
                items = [x.strip().strip('"').strip("'") for x in val[1:-1].split(",") if x.strip()]
                meta[key] = items
            else:
                meta[key] = val

    return meta, body


class SkillsRegistry:
    """Manages progressive discovery, validation, and loading of Smara skills."""

    def __init__(self, workspace_dir: Optional[Path] = None):
        self.workspace_dir = Path(workspace_dir or Path.cwd()).resolve()
        self.roots: List[Tuple[str, Path]] = [
            ("workspace", self.workspace_dir / ".smara" / "skills"),
            ("user", Path.home() / ".smara" / "skills"),
            ("builtin", Path(__file__).resolve().parent / "skills"),
        ]

    def list_skills(self, tag_filter: Optional[str] = None) -> List[Dict[str, Any]]:
        """Tier 1: Discover all skills and return compact metadata dictionary."""
        discovered: Dict[str, SkillMetadata] = {}

        for source, root in self.roots:
            if not root.exists() or not root.is_dir():
                continue

            for skill_md in root.glob("**/SKILL.md"):
                skill_dir = skill_md.parent
                skill_name = skill_dir.name

                try:
                    content = skill_md.read_text(encoding="utf-8", errors="ignore")
                    meta, _ = parse_yaml_frontmatter(content)
                except Exception as e:
                    logger.warning(f"Failed parsing skill at {skill_md}: {e}")
                    continue

                name = meta.get("name") or skill_name
                desc = meta.get("description") or "No description provided."
                ver = meta.get("version", "1.0.0")
                tags = meta.get("tags") or []
                if isinstance(tags, str):
                    tags = [t.strip() for t in tags.split(",") if t.strip()]

                # Only register if not already discovered by higher-precedence source
                if name not in discovered:
                    discovered[name] = SkillMetadata(
                        name=name,
                        description=desc,
                        version=ver,
                        tags=tags,
                        source=source,
                        skill_dir=str(skill_dir)
                    )

            # Discover legacy single-file JSON skills
            for json_file in root.glob("*.json"):
                try:
                    data = json.loads(json_file.read_text(encoding="utf-8", errors="ignore"))
                    name = data.get("name") or json_file.stem
                    if name not in discovered:
                        desc = data.get("description", "No description provided.")
                        triggers = data.get("triggers", [])
                        tags = triggers if isinstance(triggers, list) else [triggers]
                        discovered[name] = SkillMetadata(
                            name=name,
                            description=desc,
                            version=data.get("version", "1.0.0"),
                            tags=tags,
                            source=source,
                            skill_dir=str(json_file.parent)
                        )
                except Exception as e:
                    logger.warning(f"Failed parsing legacy skill json at {json_file}: {e}")

        results = [s.to_dict() for s in discovered.values()]
        if tag_filter:
            tf = tag_filter.lower().strip()
            results = [r for r in results if any(tf in t.lower() for t in r["tags"])]

        results.sort(key=lambda x: x["name"])
        return results

    def view_skill(self, skill_name: str, relative_path: Optional[str] = None) -> Dict[str, Any]:
        """
        Tier 2 & 3: Load the full markdown content or specific reference asset of a skill.
        """
        skill_name = skill_name.strip()
        skills = {s["name"]: s for s in self.list_skills()}

        if skill_name not in skills:
            available = list(skills.keys())
            return {
                "status": "error",
                "message": f"Skill '{skill_name}' not found. Available skills: {available}"
            }

        skill_meta = skills[skill_name]
        skill_dir = Path(skill_meta["skill_dir"])

        # Check for legacy JSON single-file skill
        clean_slug = re.sub(r'[^a-zA-Z0-9_-]', '_', skill_name.lower())
        json_candidates = [
            skill_dir / f"{clean_slug}.json",
            skill_dir / f"{skill_name}.json"
        ]
        for jc in json_candidates:
            if jc.exists() and not (skill_dir / "SKILL.md").exists():
                try:
                    data = json.loads(jc.read_text(encoding="utf-8", errors="ignore"))
                    instructions = data.get("instructions_md") or data.get("instructions") or ""
                    return {
                        "status": "success",
                        "skill": skill_name,
                        "metadata": {
                            "name": skill_name,
                            "description": data.get("description", ""),
                            "version": data.get("version", "1.0.0"),
                            "tags": data.get("triggers", [])
                        },
                        "instructions": instructions,
                        "available_assets": []
                    }
                except Exception as e:
                    logger.warning(f"Failed reading json skill {jc}: {e}")

        # Tier 3: Specific referenced sub-file
        if relative_path:
            clean_rel = relative_path.replace("\\", "/").lstrip("/")
            target_path = (skill_dir / clean_rel).resolve()
            # Path traversal safety check
            if not str(target_path).startswith(str(skill_dir.resolve())):
                return {"status": "error", "message": "Access denied: Path traversal detected."}

            if not target_path.exists() or not target_path.is_file():
                return {"status": "error", "message": f"Referenced file '{clean_rel}' not found in skill '{skill_name}'."}

            try:
                text = target_path.read_text(encoding="utf-8", errors="ignore")
                return {
                    "status": "success",
                    "skill": skill_name,
                    "file": clean_rel,
                    "content": text
                }
            except Exception as e:
                return {"status": "error", "message": f"Error reading file '{clean_rel}': {e}"}

        # Tier 2: Main SKILL.md
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            return {"status": "error", "message": f"SKILL.md not found in {skill_dir}."}

        # Discover Tier 3 supporting assets (references/, examples/, templates/)
        available_assets = []
        for sub in ("references", "examples", "templates"):
            subdir = skill_dir / sub
            if subdir.exists() and subdir.is_dir():
                for f in subdir.rglob("*"):
                    if f.is_file():
                        rel = str(f.relative_to(skill_dir)).replace("\\", "/")
                        available_assets.append(rel)

        try:
            raw_text = skill_md.read_text(encoding="utf-8", errors="ignore")
            meta, body = parse_yaml_frontmatter(raw_text)
            return {
                "status": "success",
                "skill": skill_name,
                "metadata": meta,
                "instructions": body or raw_text,
                "available_assets": available_assets
            }
        except Exception as e:
            return {"status": "error", "message": f"Failed reading SKILL.md: {e}"}

    def create_skill(
        self,
        name: str,
        description: str,
        tags: List[str],
        instructions: str,
        assets: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """Scaffold a new progressive skill folder under .smara/skills/{slug}/SKILL.md."""
        clean_slug = re.sub(r'[^a-zA-Z0-9_-]', '-', name.strip().lower()).strip("-")
        target_dir = self.workspace_dir / ".smara" / "skills" / clean_slug
        target_dir.mkdir(parents=True, exist_ok=True)

        tags_str = ", ".join(tags)
        frontmatter = f"---\nname: {name}\ndescription: {description}\nversion: 1.0.0\ntags: [{tags_str}]\nsource: workspace\n---\n\n"
        skill_md = target_dir / "SKILL.md"
        skill_md.write_text(frontmatter + instructions.strip() + "\n", encoding="utf-8")

        if assets:
            for rel_path, asset_content in assets.items():
                p = (target_dir / rel_path.replace("\\", "/").lstrip("/")).resolve()
                if str(p).startswith(str(target_dir.resolve())):
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_text(asset_content, encoding="utf-8")

        return {
            "status": "success",
            "name": name,
            "skill_dir": str(target_dir),
            "skill_md": str(skill_md)
        }


# Global default instance
_default_skills: Optional[SkillsRegistry] = None

def get_default_skills_registry(workspace_dir: Optional[Path] = None) -> SkillsRegistry:
    global _default_skills
    if _default_skills is None or workspace_dir is not None:
        _default_skills = SkillsRegistry(workspace_dir=workspace_dir)
    return _default_skills
