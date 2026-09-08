"""Typed research evidence with deterministic provenance validation."""
from __future__ import annotations
import hashlib
from dataclasses import asdict, dataclass
from datetime import datetime,timezone
from typing import Literal
from urllib.parse import urljoin
from .research import canonical_source_url

EvidenceKind=Literal["search_snippet","fetched_passage","pdf_page","pdf_table","image_ocr"]

@dataclass(frozen=True)
class EvidenceRecord:
    id:str; kind:EvidenceKind; canonical_url:str; redirect_chain:tuple[str,...]; retrieved_at:str; content_sha256:str; extraction_version:str; text:str; start:int|None=None; end:int|None=None; page:int|None=None; bbox:tuple[float,float,float,float]|None=None; row:int|None=None; column:int|None=None; confidence:float=1.0; text_sha256:str=""; source_artifact_id:str|None=None

class EvidenceIndex:
    def __init__(self,artifact_store=None): self.records={}; self.content_publications={};self.artifact_store=artifact_store;self.failures=[]
    def add(self,*,kind:EvidenceKind,url:str,content:bytes,text:str,redirect_chain=(),extraction_version="text-v1",**location):
        if kind not in {"search_snippet","fetched_passage","pdf_page","pdf_table","image_ocr"}: raise ValueError("unknown evidence kind")
        canonical=canonical_source_url(url); digest=hashlib.sha256(content).hexdigest(); ident=hashlib.sha256(f"{kind}:{canonical}:{digest}:{location}".encode()).hexdigest()
        artifact_id=self.artifact_store.put(content)[0] if self.artifact_store is not None else None
        record=EvidenceRecord(id=ident,kind=kind,canonical_url=canonical,redirect_chain=tuple(canonical_source_url(item) for item in redirect_chain),retrieved_at=datetime.now(timezone.utc).isoformat(),content_sha256=digest,extraction_version=extraction_version,text=text,text_sha256=hashlib.sha256(text.encode()).hexdigest(),source_artifact_id=artifact_id,**location)
        self.records.setdefault(ident,record); self.content_publications.setdefault(digest,set()).add(canonical); return self.records[ident]
    def record_failure(self,url:str,error:str):
        failure={"canonical_url":canonical_source_url(url),"retrieved_at":datetime.now(timezone.utc).isoformat(),"error":str(error)[:500]};self.failures.append(failure);return failure
    def support(self,evidence_id:str,claim_text:str,*,require_fetched=True):
        record=self.records[evidence_id]
        if require_fetched and record.kind=="search_snippet": return False,"snippet_is_discovery_only"
        if not record.text.strip(): return False,"empty_evidence"
        if record.kind=="image_ocr" and record.confidence<0.8:return False,"uncertain_ocr"
        terms={item.lower() for item in claim_text.split() if len(item)>3}; source=record.text.lower(); missing=[term for term in terms if term not in source]
        return (not missing,"supported" if not missing else f"missing_terms:{','.join(missing[:5])}")
    def validate_location(self,evidence_id:str,original:bytes):
        record=self.records[evidence_id]
        if hashlib.sha256(original).hexdigest()!=record.content_sha256:return False
        if hashlib.sha256(record.text.encode()).hexdigest()!=record.text_sha256:return False
        if record.kind=="fetched_passage":return record.start is not None and record.end is not None and original.decode("utf-8",errors="replace")[record.start:record.end]==record.text
        if record.kind=="pdf_page":return bool(record.text) and record.page is not None and record.page>0 and record.bbox is not None
        if record.kind=="pdf_table":return bool(record.text) and record.page is not None and record.page>0 and record.row is not None and record.column is not None
        if record.kind=="image_ocr":return bool(record.text) and record.bbox is not None and 0<=record.confidence<=1
        return bool(record.text)
    def to_dict(self): return {"version":1,"records":[asdict(item) for item in self.records.values()],"failures":list(self.failures)}
    @classmethod
    def from_dict(cls,value):
        if int(value.get("version",0))!=1: raise ValueError("unsupported evidence index version")
        index=cls()
        for item in value.get("records",[]):
            record=EvidenceRecord(**{**item,"redirect_chain":tuple(item.get("redirect_chain",())),"bbox":tuple(item["bbox"]) if item.get("bbox") else None})
            index.records[record.id]=record; index.content_publications.setdefault(record.content_sha256,set()).add(record.canonical_url)
        index.failures=[dict(item) for item in value.get("failures",[]) if isinstance(item,dict)]
        return index
