"""Quarantined skill candidates and held-out promotion records."""
from __future__ import annotations
import json,os,tempfile
from dataclasses import asdict,dataclass,field
from pathlib import Path
@dataclass
class SkillCandidate:
    name:str;procedure_version:int;provenance:str;capabilities:tuple[str,...];procedure:str;evaluations:list[dict]=field(default_factory=list);status:str="quarantined"
class SkillCandidateStore:
    def __init__(self,root):self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True)
    def save(self,candidate):
        if any(token in candidate.procedure.lower() for token in ("api_key=","password=","gold answer","benchmark answer")):raise ValueError("candidate contains prohibited material")
        path=self.root/f"{candidate.name}.json"; fd,tmp=tempfile.mkstemp(dir=self.root)
        with os.fdopen(fd,"w",encoding="utf-8") as out:json.dump(asdict(candidate),out,indent=2)
        os.replace(tmp,path)
    def promote(self,candidate,held_out_results):
        if not held_out_results or not all(item.get("passed") for item in held_out_results):raise ValueError("held-out transfer gate failed")
        candidate.evaluations.extend(held_out_results);candidate.status="promoted";self.save(candidate)
    def revoke(self,candidate,reason):candidate.status="revoked";candidate.evaluations.append({"revoked":True,"reason":reason});self.save(candidate)
