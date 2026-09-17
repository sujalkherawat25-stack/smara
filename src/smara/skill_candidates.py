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
    def promote_matrix(self,candidate,gate_summary):
        """Promote only after the stricter role/quality/safety matrix passes."""
        if not isinstance(gate_summary,dict) or not gate_summary.get("gate_passed"):
            raise ValueError("delegation matrix gate failed")
        required=("overall_rate","role_rates","false_completions","safety_violations","budget_overruns","recursive_delegations")
        if any(key not in gate_summary for key in required):
            raise ValueError("delegation matrix summary is incomplete")
        if float(gate_summary["overall_rate"]) < .90 or any(float(value) < .80 for value in (gate_summary.get("role_rates") or {}).values()):
            raise ValueError("delegation matrix threshold failed")
        if any(int(gate_summary[key]) for key in required[2:]):
            raise ValueError("delegation matrix safety gate failed")
        candidate.evaluations.append({"kind":"delegation_matrix","summary":gate_summary});candidate.status="promoted";self.save(candidate)
    def revoke(self,candidate,reason):candidate.status="revoked";candidate.evaluations.append({"revoked":True,"reason":reason});self.save(candidate)
