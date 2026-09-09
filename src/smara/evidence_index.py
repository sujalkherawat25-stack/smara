"""Typed research evidence with conservative deterministic claim validation.

The deterministic checker is deliberately an acceptance *floor*.  It can
prove exact/structured facts and reject obvious contradictions, but it never
turns loose word overlap into semantic evidence.
"""
from __future__ import annotations
import hashlib
import math
import re
import unicodedata
from dataclasses import asdict, dataclass
from datetime import datetime,timezone
from pathlib import Path
from typing import Literal
from urllib.parse import urljoin
from .research import canonical_source_url

EvidenceKind=Literal["search_snippet","fetched_passage","pdf_page","pdf_table","image_ocr"]
ClaimState=Literal["supported","refuted","insufficient"]

_NEGATIONS={"no","not","never","neither","nor","without","cannot","can't","didn't","doesn't","isn't","wasn't","weren't"}
_STOPWORDS={"a","an","and","are","as","at","be","been","being","by","for","from","has","have","had","in","into","is","it","its","of","on","or","that","the","their","there","these","this","those","to","was","were","with"}
_CAUSAL={"because","cause","causes","caused","causing","due","leads","led","results","resulted","therefore"}
_UNIT_ALIASES={
    "kg":("mass",1.0),"kgs":("mass",1.0),"kilogram":("mass",1.0),"kilograms":("mass",1.0),
    "g":("mass",.001),"gram":("mass",.001),"grams":("mass",.001),
    "lb":("mass",.45359237),"lbs":("mass",.45359237),"pound":("mass",.45359237),"pounds":("mass",.45359237),
    "km":("length",1000.0),"kilometer":("length",1000.0),"kilometers":("length",1000.0),
    "m":("length",1.0),"meter":("length",1.0),"meters":("length",1.0),
    "cm":("length",.01),"centimeter":("length",.01),"centimeters":("length",.01),
    "%":("percent",1.0),"percent":("percent",1.0),"percentage":("percent",1.0),
}

def _normalise(value:str) -> str:
    value=unicodedata.normalize("NFKC",str(value)).replace("\N{MINUS SIGN}","-")
    return re.sub(r"\s+"," ",value).strip().lower()

def _tokens(value:str) -> list[str]:
    return re.findall(r"[a-z]+(?:'[a-z]+)?|[-+]?\d+(?:[.,]\d+)*|%",_normalise(value))

def _meaningful(value:str) -> list[str]:
    return [token for token in _tokens(value) if token not in _STOPWORDS]

def _sentences(value:str) -> list[str]:
    return [item.strip() for item in re.split(r"(?<=[.!?;])\s+|[\r\n]+",_normalise(value)) if item.strip()]

def _quantities(value:str) -> list[tuple[float,str,str]]:
    result=[]
    units="|".join(re.escape(item) for item in sorted(_UNIT_ALIASES,key=len,reverse=True))
    pattern=rf"(?<![\w.])([-+]?\d+(?:[.,]\d+)*)\s*({units})?(?![a-zA-Z])"
    for match in re.finditer(pattern,_normalise(value)):
        raw=match.group(1).replace(",","")
        try:number=float(raw)
        except ValueError:continue
        unit=(match.group(2) or "").lower();dimension,factor=_UNIT_ALIASES.get(unit,(unit or "number",1.0))
        result.append((number*factor,dimension,unit))
    return result

def _quantity_equal(left:tuple[float,str,str],right:tuple[float,str,str]) -> bool:
    return left[1]==right[1] and math.isclose(left[0],right[0],rel_tol=1e-9,abs_tol=1e-12)

@dataclass(frozen=True)
class ClaimJudgment:
    state:ClaimState
    reason:str
    evidence_id:str
    passage_sha256:str

@dataclass(frozen=True)
class EvidenceRecord:
    id:str; kind:EvidenceKind; canonical_url:str; redirect_chain:tuple[str,...]; retrieved_at:str; content_sha256:str; extraction_version:str; text:str; start:int|None=None; end:int|None=None; page:int|None=None; bbox:tuple[float,float,float,float]|None=None; row:int|None=None; column:int|None=None; confidence:float=1.0; text_sha256:str=""; source_artifact_id:str|None=None; extraction_sha256:str=""; extraction_artifact_id:str|None=None

class EvidenceIndex:
    def __init__(self,artifact_store=None): self.records={}; self.content_publications={};self.artifact_store=artifact_store;self.failures=[]
    def add(self,*,kind:EvidenceKind,url:str,content:bytes,text:str,redirect_chain=(),extraction_version="text-v1",extracted_content:bytes|None=None,**location):
        if kind not in {"search_snippet","fetched_passage","pdf_page","pdf_table","image_ocr"}: raise ValueError("unknown evidence kind")
        canonical=canonical_source_url(url); digest=hashlib.sha256(content).hexdigest(); ident=hashlib.sha256(f"{kind}:{canonical}:{digest}:{location}".encode()).hexdigest()
        artifact_id=self.artifact_store.put(content)[0] if self.artifact_store is not None else None
        extraction=content if extracted_content is None else extracted_content
        extraction_digest=hashlib.sha256(extraction).hexdigest()
        extraction_artifact_id=(artifact_id if extraction_digest==digest else self.artifact_store.put(extraction,".extracted")[0]) if self.artifact_store is not None else None
        record=EvidenceRecord(id=ident,kind=kind,canonical_url=canonical,redirect_chain=tuple(canonical_source_url(item) for item in redirect_chain),retrieved_at=datetime.now(timezone.utc).isoformat(),content_sha256=digest,extraction_version=extraction_version,text=text,text_sha256=hashlib.sha256(text.encode()).hexdigest(),source_artifact_id=artifact_id,extraction_sha256=extraction_digest,extraction_artifact_id=extraction_artifact_id,**location)
        self.records.setdefault(ident,record); self.content_publications.setdefault(digest,set()).add(canonical); return self.records[ident]
    def record_failure(self,url:str,error:str):
        failure={"canonical_url":canonical_source_url(url),"retrieved_at":datetime.now(timezone.utc).isoformat(),"error":str(error)[:500]};self.failures.append(failure);return failure
    def judge(self,evidence_id:str,claim_text:str,*,require_fetched=True) -> ClaimJudgment:
        record=self.records[evidence_id]; passage_hash=record.text_sha256
        if require_fetched and record.kind=="search_snippet":return ClaimJudgment("insufficient","snippet_is_discovery_only",evidence_id,passage_hash)
        source=_normalise(record.text); claim=_normalise(claim_text)
        if not source:return ClaimJudgment("insufficient","empty_evidence",evidence_id,passage_hash)
        if record.kind=="image_ocr" and record.confidence<0.8:return ClaimJudgment("insufficient","uncertain_ocr",evidence_id,passage_hash)
        terms=_meaningful(claim)
        if not terms:return ClaimJudgment("insufficient","empty_meaningful_claim",evidence_id,passage_hash)
        source_sentences=_sentences(source) or [source]
        claim_negated=any(token in _NEGATIONS for token in _tokens(claim))
        claim_quantities=_quantities(claim)
        claim_terms=[item for item in terms if item not in _NEGATIONS and not re.fullmatch(r"[-+]?\d+(?:[.,]\d+)*|%",item) and item not in _UNIT_ALIASES]
        best_reason="no_single_passage_support"
        for sentence in source_sentences:
            sentence_tokens=_tokens(sentence); sentence_set=set(sentence_tokens)
            lexical_missing=[term for term in claim_terms if term not in sentence_set]
            if lexical_missing:
                best_reason=f"missing_terms:{','.join(lexical_missing[:5])}"
                continue
            sentence_negated=any(token in _NEGATIONS for token in sentence_tokens)
            if claim_negated!=sentence_negated:
                return ClaimJudgment("refuted","negation_conflict",evidence_id,passage_hash)
            source_quantities=_quantities(sentence)
            if claim_quantities:
                unmatched=[item for item in claim_quantities if not any(_quantity_equal(item,other) for other in source_quantities)]
                if unmatched:
                    same_dimensions={item[1] for item in claim_quantities}&{item[1] for item in source_quantities}
                    state="refuted" if same_dimensions else "insufficient"
                    return ClaimJudgment(state,"quantity_conflict" if same_dimensions else "quantity_missing",evidence_id,passage_hash)
            claim_causal=bool(set(_tokens(claim))&_CAUSAL); source_causal=bool(set(sentence_tokens)&_CAUSAL)
            if claim_causal and not source_causal:
                return ClaimJudgment("insufficient","causal_relation_not_stated",evidence_id,passage_hash)
            # Lexical support is accepted only within one exact passage and
            # after polarity/quantity/relation checks.  Paraphrases remain
            # insufficient for a separate semantic validator to assess.
            return ClaimJudgment("supported","structured_passage_match",evidence_id,passage_hash)
        return ClaimJudgment("insufficient",best_reason,evidence_id,passage_hash)
    def support(self,evidence_id:str,claim_text:str,*,require_fetched=True):
        judgment=self.judge(evidence_id,claim_text,require_fetched=require_fetched)
        return judgment.state=="supported",judgment.reason
    def _artifact_bytes(self,artifact_id:str|None) -> bytes|None:
        if not artifact_id or self.artifact_store is None:return None
        matches=list(Path(self.artifact_store.root).glob(f"{artifact_id}.*"))
        if len(matches)!=1:return None
        try:return matches[0].read_bytes()
        except OSError:return None
    def validate_artifact(self,evidence_id:str) -> tuple[bool,str]:
        record=self.records.get(evidence_id)
        if record is None:return False,"missing_evidence"
        if not record.source_artifact_id:return False,"missing_source_artifact"
        if self.artifact_store is None:return False,"artifact_store_unavailable"
        data=self._artifact_bytes(record.source_artifact_id)
        if data is None:return False,"missing_source_artifact"
        if hashlib.sha256(data).hexdigest()!=record.source_artifact_id:return False,"stale_source_artifact"
        if hashlib.sha256(data).hexdigest()!=record.content_sha256:return False,"content_hash_mismatch"
        extracted=self._artifact_bytes(record.extraction_artifact_id)
        if record.extraction_artifact_id and extracted is None:return False,"missing_extraction_artifact"
        if extracted is not None and hashlib.sha256(extracted).hexdigest()!=record.extraction_sha256:return False,"stale_extraction_artifact"
        location_source=extracted if extracted is not None else data
        if not self.validate_location(evidence_id,data,extracted=location_source):return False,"invalid_passage_location"
        return True,"valid"
    def validate_location(self,evidence_id:str,original:bytes,*,extracted:bytes|None=None):
        record=self.records[evidence_id]
        if hashlib.sha256(original).hexdigest()!=record.content_sha256:return False
        if hashlib.sha256(record.text.encode()).hexdigest()!=record.text_sha256:return False
        location_source=original if extracted is None else extracted
        if record.extraction_sha256 and hashlib.sha256(location_source).hexdigest()!=record.extraction_sha256:return False
        if record.kind=="fetched_passage":return record.start is not None and record.end is not None and location_source.decode("utf-8",errors="replace")[record.start:record.end]==record.text
        if record.kind=="pdf_page":return bool(record.text) and record.page is not None and record.page>0 and record.bbox is not None
        if record.kind=="pdf_table":
            located=bool(record.text) and record.page is not None and record.page>0 and record.row is not None and record.column is not None
            if located and record.start is not None and record.end is not None:located=location_source.decode("utf-8",errors="replace")[record.start:record.end]==record.text
            return located
        if record.kind=="image_ocr":return bool(record.text) and record.bbox is not None and 0<=record.confidence<=1
        return bool(record.text)
    def to_dict(self): return {"version":1,"records":[asdict(item) for item in self.records.values()],"failures":list(self.failures)}
    @classmethod
    def from_dict(cls,value,artifact_store=None):
        if int(value.get("version",0))!=1: raise ValueError("unsupported evidence index version")
        index=cls(artifact_store)
        for item in value.get("records",[]):
            record=EvidenceRecord(**{**item,"redirect_chain":tuple(item.get("redirect_chain",())),"bbox":tuple(item["bbox"]) if item.get("bbox") else None})
            index.records[record.id]=record; index.content_publications.setdefault(record.content_sha256,set()).add(record.canonical_url)
        index.failures=[dict(item) for item in value.get("failures",[]) if isinstance(item,dict)]
        return index
