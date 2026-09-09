"""Deterministic evidence precision/coverage scoring for sealed corpora."""
from __future__ import annotations
from dataclasses import asdict,dataclass
from typing import Iterable
from .evidence_index import EvidenceIndex

@dataclass(frozen=True)
class ClaimCheck:
    claim:str
    evidence_ids:tuple[str,...]
    require_fetched:bool=True
    require_artifacts:bool=False

def score_claims(index:EvidenceIndex,claims:Iterable[ClaimCheck]):
    checks=[];citations=0;supported_citations=0;supported_claims=0
    for claim in claims:
        results=[]
        for evidence_id in claim.evidence_ids:
            citations+=1
            if evidence_id not in index.records:results.append({"evidence_id":evidence_id,"supported":False,"reason":"missing_evidence"});continue
            judgment=index.judge(evidence_id,claim.claim,require_fetched=claim.require_fetched);supported=judgment.state=="supported";reason=judgment.reason
            if supported and claim.require_artifacts:
                supported,reason=index.validate_artifact(evidence_id)
            supported_citations+=int(supported);results.append({"evidence_id":evidence_id,"supported":supported,"state":judgment.state,"reason":reason,"passage_sha256":judgment.passage_sha256})
        claim_supported=any(item["supported"] for item in results);supported_claims+=int(claim_supported);checks.append({**asdict(claim),"supported":claim_supported,"citations":results})
    total=len(checks)
    return {"claims":checks,"claim_count":total,"supported_claims":supported_claims,"evidence_precision":supported_citations/citations if citations else 0.0,"evidence_coverage":supported_claims/total if total else 0.0,"retrieval_failures":list(index.failures)}
