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
import hashlib
import logging
import os
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("smara.skills_system")

SKILL_LIFECYCLE_STATES = frozenset({"candidate", "quarantined", "promoted", "rejected", "revoked", "superseded"})
_SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")
_SKILL_SECRET = re.compile(r"(?:sk-[A-Za-z0-9_-]{12,}|api[_ -]?key\s*[:=]|authorization\s*:\s*bearer)", re.I)
_UNSAFE_PLAYBOOK = re.compile(r"(?:curl\s+[^\n|]+\|\s*(?:sh|bash)|rm\s+-rf|Invoke-Expression|\biex\s*\()", re.I)


class SkillLifecycleManager:
    """Durable, test-gated promotion state for learned/project skills.

    Promotion metadata is kept beside the skill and never executes skill
    content.  The explicit gate prevents an unreviewed model-generated
    workflow from silently becoming an active local capability.
    """

    def __init__(self, workspace_dir: Path | str):
        self.workspace_dir = Path(workspace_dir).expanduser().resolve()
        self.root = self.workspace_dir / ".smara" / "skills"
        self.path = self.root / ".lifecycle.json"

    def _read(self) -> dict[str, dict[str, Any]]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    def _write(self, value: dict[str, dict[str, Any]]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.path)

    def _content_hash(self, name: str) -> str:
        """Hash the reviewed playbook, never a mutable display label."""
        candidates = [self.root / name / "SKILL.md", self.root / f"{name}.json"]
        for candidate in candidates:
            try:
                # Hash canonical text so Windows newline normalization cannot
                # make a reviewed playbook look tampered after a restart.
                return hashlib.sha256(candidate.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
            except OSError:
                continue
        return ""

    @staticmethod
    def validate_playbook(content: str, *, version: str, risk: str = "confirm") -> dict[str, Any]:
        """Return deterministic, secret-safe validation evidence for a skill.

        A playbook is data, not an executable installation package.  These
        checks deliberately reject common secret and shell bootstrap patterns;
        capability approval still applies when the playbook is invoked.
        """
        failures: list[str] = []
        if not _SEMVER.fullmatch(str(version or "")):
            failures.append("version must use semantic versioning")
        if not content.strip():
            failures.append("playbook is empty")
        if len(content.encode("utf-8")) > 128_000:
            failures.append("playbook exceeds 128 KiB")
        if _SKILL_SECRET.search(content):
            failures.append("playbook appears to contain a credential")
        if _UNSAFE_PLAYBOOK.search(content):
            failures.append("playbook contains an unsafe shell bootstrap")
        if risk not in {"read_only", "confirm"}:
            failures.append("risk must be read_only or confirm")
        return {
            "passed": not failures,
            "failures": failures,
            "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "validated_at": time.time(),
        }

    @staticmethod
    def _normalise(values: dict[str, Any]) -> dict[str, Any]:
        """Migrate the original name-keyed ledger without discarding history."""
        migrated: dict[str, Any] = {}
        for name, record in values.items():
            if isinstance(record, dict) and isinstance(record.get("versions"), dict):
                migrated[name] = record
                continue
            if not isinstance(record, dict):
                continue
            version = str(record.get("version") or "1.0.0")
            migrated[name] = {
                "name": name,
                "active_version": version if record.get("state") == "promoted" else None,
                "versions": {version: record},
                "history": [],
            }
        return migrated

    def _ledger(self) -> dict[str, Any]:
        return self._normalise(self._read())

    @staticmethod
    def _current(entry: dict[str, Any], version: str | None = None) -> dict[str, Any] | None:
        versions = entry.get("versions") or {}
        selected = version or entry.get("active_version")
        if selected and isinstance(versions.get(selected), dict):
            return versions[selected]
        return next(iter(versions.values()), None)

    @staticmethod
    def gate_passed(gate: dict[str, Any]) -> tuple[bool, list[str]]:
        failures: list[str] = []
        if gate.get("passed") is not True:
            failures.append("gate did not report passed=true")
        if float(gate.get("overall_rate", 0) or 0) < 0.90:
            failures.append("overall_rate must be at least 0.90")
        for name, rate in (gate.get("category_rates") or {}).items():
            if float(rate or 0) < 0.80:
                failures.append(f"category {name} is below 0.80")
        if int(gate.get("false_completions", 0) or 0) != 0:
            failures.append("false completions must be zero")
        if int(gate.get("safety_violations", 0) or 0) != 0:
            failures.append("safety violations must be zero")
        if gate.get("reproducible") is not True:
            failures.append("gate must be reproducible")
        return not failures, failures

    def submit_candidate(self, name: str, *, source_run_id: str = "", gate: dict[str, Any] | None = None,
                         version: str = "1.0.0", risk: str = "confirm", automatic_reuse: bool = False) -> dict[str, Any]:
        name = str(name).strip()
        if not re.fullmatch(r"[a-z][a-z0-9_-]{1,63}", name):
            raise ValueError("skill name must be a lowercase bounded identifier")
        if not _SEMVER.fullmatch(version):
            raise ValueError("skill version must use semantic versioning")
        gate = dict(gate or {})
        passed, failures = self.gate_passed(gate)
        state = "candidate" if passed else "quarantined"
        content_hash = self._content_hash(name)
        record = {"name": name, "version": version, "state": state, "source_run_id": str(source_run_id)[:160],
                  "gate": gate, "failures": failures, "updated_at": time.time(),
                  "promotion_id": f"promotion_{uuid.uuid4().hex}", "content_sha256": content_hash,
                  "risk": risk, "automatic_reuse": bool(automatic_reuse), "validation": None}
        values = self._ledger(); entry = values.setdefault(name, {"name": name, "active_version": None, "versions": {}, "history": []})
        entry["versions"][version] = record
        entry.setdefault("history", []).append({"event": "candidate_submitted", "version": version, "at": time.time()})
        self._write(values)
        return record

    def validate(self, name: str, *, version: str | None = None) -> dict[str, Any]:
        values = self._ledger(); entry = values.get(name)
        record = self._current(entry or {}, version)
        if not record:
            raise KeyError(name)
        content_path = self.root / name / "SKILL.md"
        try:
            content = content_path.read_text(encoding="utf-8")
        except OSError:
            content = ""
        # Keep pre-existing metadata-only promotion records operable.  They
        # remain ineligible for automatic reuse because they have no pinned
        # playbook digest; newly learned/project skills always materialize a
        # SKILL.md and receive the full content validation below.
        if not content and not record.get("content_sha256"):
            validation = {"passed": True, "failures": [], "content_sha256": "", "validated_at": time.time(), "legacy_metadata_only": True}
            record["validation"] = validation
            entry["versions"][record["version"]] = record; self._write(values)
            return validation
        validation = self.validate_playbook(content, version=str(record.get("version", "")), risk=str(record.get("risk", "confirm")))
        record["validation"] = validation
        # ``content_sha256`` is the pinned approval digest.  Never replace it
        # during validation; a changed file must make automatic reuse fail.
        entry["versions"][record["version"]] = record; self._write(values)
        return validation

    def promote(self, name: str, *, gate: dict[str, Any] | None = None, reviewer: str = "local-user", version: str | None = None) -> dict[str, Any]:
        values = self._ledger(); entry = values.get(name)
        record = self._current(entry or {}, version)
        if not record:
            raise KeyError(name)
        effective = dict(gate or record.get("gate") or {})
        passed, failures = self.gate_passed(effective)
        if not passed:
            record["state"] = "quarantined"; record["failures"] = failures
            entry["versions"][record["version"]] = record; self._write(values)
            raise ValueError("skill promotion gate failed: " + "; ".join(failures))
        validation = self.validate(name, version=record["version"])
        if not validation["passed"]:
            record["state"] = "quarantined"; record["failures"] = list(validation["failures"])
            entry["versions"][record["version"]] = record; self._write(values)
            raise ValueError("skill validation failed: " + "; ".join(validation["failures"]))
        prior = entry.get("active_version")
        if prior and prior != record["version"] and prior in entry["versions"]:
            entry["versions"][prior]["state"] = "superseded"
        record.update({"state": "promoted", "gate": effective, "reviewer": reviewer[:160], "failures": [], "updated_at": time.time()})
        entry["versions"][record["version"]] = record; entry["active_version"] = record["version"]
        entry.setdefault("history", []).append({"event": "promoted", "version": record["version"], "previous": prior, "at": time.time(), "reviewer": reviewer[:160]})
        self._write(values)
        return record

    def set_state(self, name: str, state: str, *, reason: str = "", version: str | None = None) -> dict[str, Any]:
        if state not in SKILL_LIFECYCLE_STATES:
            raise ValueError(f"invalid lifecycle state: {state}")
        values = self._ledger(); entry = values.get(name); record = self._current(entry or {}, version)
        if not record:
            raise KeyError(name)
        record.update({"state": state, "reason": reason[:500], "updated_at": time.time()})
        entry["versions"][record["version"]] = record
        if entry.get("active_version") == record["version"] and state != "promoted": entry["active_version"] = None
        entry.setdefault("history", []).append({"event": state, "version": record["version"], "reason": reason[:500], "at": time.time()})
        self._write(values)
        return record

    def rollback(self, name: str, version: str) -> dict[str, Any]:
        values = self._ledger(); entry = values.get(name)
        if not entry or not isinstance(entry.get("versions", {}).get(version), dict):
            raise KeyError(name)
        target = entry["versions"][version]
        if target.get("state") not in {"promoted", "superseded"}:
            raise ValueError("rollback target must be a previously promoted version")
        current = entry.get("active_version")
        if current and current != version and current in entry["versions"]:
            entry["versions"][current]["state"] = "superseded"
        target["state"] = "promoted"; target["updated_at"] = time.time()
        entry["active_version"] = version
        entry.setdefault("history", []).append({"event": "rolled_back", "version": version, "previous": current, "at": time.time()})
        self._write(values)
        return target

    def reuse_decision(self, name: str, query: str, *, version: str | None = None) -> dict[str, Any]:
        """Conservative auto-reuse decision.  This never grants tool authority."""
        values = self._ledger(); entry = values.get(name); record = self._current(entry or {}, version)
        if not record:
            return {"eligible": False, "reason": "skill is not registered"}
        validation = record.get("validation") or self.validate(name, version=record["version"])
        gate = record.get("gate") or {}
        triggers = " ".join(str(item) for item in gate.get("triggers", []))
        matching = not triggers or any(token in query.lower() for token in triggers.lower().split() if len(token) > 2)
        independent = int(gate.get("independent_runs", 0) or 0)
        digest_matches = bool(record.get("content_sha256")) and record.get("content_sha256") == validation.get("content_sha256")
        eligible = bool(record.get("state") == "promoted" and record.get("automatic_reuse") and record.get("risk") == "read_only" and digest_matches and validation.get("passed") and independent >= 3 and matching)
        return {"eligible": eligible, "name": name, "version": record.get("version"), "reason": "eligible" if eligible else "requires promoted read-only evidence with three independent runs and a matching trigger", "content_sha256": validation.get("content_sha256")}

    def state_for(self, name: str, version: str | None = None) -> str:
        entry = self._ledger().get(name)
        record = self._current(entry or {}, version)
        return str(record.get("state")) if record else "promoted"

    def list(self, *, include_quarantined: bool = True) -> list[dict[str, Any]]:
        values = self._ledger()
        rows = []
        for entry in values.values():
            record = self._current(entry)
            if record:
                rows.append({**record, "active_version": entry.get("active_version"), "versions": sorted(entry.get("versions", {})), "history": entry.get("history", [])[-20:]})
        if not include_quarantined:
            rows = [row for row in rows if row.get("state") == "promoted"]
        return sorted(rows, key=lambda row: str(row.get("name", "")))


def _is_within(path: Path, root: Path) -> bool:
    """Check resolved ancestor containment without string-prefix aliases."""
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


@dataclass
class SkillMetadata:
    name: str
    description: str
    version: str = "1.0.0"
    tags: List[str] = field(default_factory=list)
    platforms: List[str] = field(default_factory=list)
    source: str = "workspace"  # "workspace", "user", "builtin"
    skill_dir: str = ""
    lifecycle: str = "promoted"

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

    def __init__(self, workspace_dir: Optional[Path] = None, workspace_trusted: Optional[bool] = None):
        self.workspace_dir = Path(workspace_dir or Path.cwd()).resolve()
        self.workspace_trusted = workspace_trusted
        self.roots: List[Tuple[str, Path]] = [
            ("workspace", self.workspace_dir / ".smara" / "skills"),
            ("user", Path.home() / ".smara" / "skills"),
            ("builtin", Path(__file__).resolve().parent / "skills"),
        ]
        self.lifecycle = SkillLifecycleManager(self.workspace_dir)

    def list_skills(self, tag_filter: Optional[str] = None) -> List[Dict[str, Any]]:
        """Tier 1: Discover all skills and return compact metadata dictionary."""
        discovered: Dict[str, SkillMetadata] = {}

        for source, root in self.roots:
            if source == "workspace" and self.workspace_trusted is False:
                continue
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
                    lifecycle = self.lifecycle.state_for(str(name), str(ver))
                    discovered[name] = SkillMetadata(
                        name=name,
                        description=desc,
                        version=ver,
                        tags=tags,
                        source=source,
                        skill_dir=str(skill_dir), lifecycle=lifecycle
                    )

            # Discover legacy single-file JSON skills
            for json_file in root.glob("*.json"):
                if json_file.name == ".lifecycle.json":
                    continue
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
                            skill_dir=str(json_file.parent), lifecycle=self.lifecycle.state_for(str(name), str(data.get("version", "1.0.0")))
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
            if not _is_within(target_path, skill_dir):
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
        frontmatter = f"---\nname: {name}\ndescription: {description}\nversion: 1.0.0\ntags: [{tags_str}]\nsource: workspace\nrisk: read_only\nautomatic_reuse: false\n---\n\n"
        skill_md = target_dir / "SKILL.md"
        skill_md.write_text(frontmatter + instructions.strip() + "\n", encoding="utf-8")

        if assets:
            for rel_path, asset_content in assets.items():
                p = (target_dir / rel_path.replace("\\", "/").lstrip("/")).resolve()
                if _is_within(p, target_dir):
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_text(asset_content, encoding="utf-8")
                else:
                    return {
                        "status": "error",
                        "message": f"Access denied: Asset path '{rel_path}' escapes the skill directory.",
                    }

        return {
            "status": "success",
            "name": name,
            "skill_dir": str(target_dir),
            "skill_md": str(skill_md)
        }


# Global default instance
_default_skills: Optional[SkillsRegistry] = None

def get_default_skills_registry(
    workspace_dir: Optional[Path] = None,
    workspace_trusted: Optional[bool] = None,
) -> SkillsRegistry:
    global _default_skills
    if _default_skills is None or workspace_dir is not None or workspace_trusted is not None:
        _default_skills = SkillsRegistry(workspace_dir=workspace_dir, workspace_trusted=workspace_trusted)
    return _default_skills
