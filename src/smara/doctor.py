from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from .harness import SessionEngine,ToolCall


def _entry(configured:bool,available:bool,tested:bool,detail:str)->dict[str,Any]:
    return {"configured":configured,"available":available,"tested":tested,"status":"tested" if tested else "available" if available else "unavailable","detail":detail}


def diagnose(workspace:Path,*,test_browser:bool=True)->dict[str,Any]:
    workspace=Path(workspace).resolve();checks={}
    try:
        with tempfile.NamedTemporaryFile(prefix=".smara-doctor-",dir=workspace,delete=True) as handle:handle.write(b"ok");handle.flush()
        checks["workspace_writable"]=_entry(True,True,True,str(workspace))
    except OSError as exc:checks["workspace_writable"]=_entry(True,False,False,type(exc).__name__)
    try:
        with tempfile.TemporaryDirectory(prefix="smara-doctor-") as raw:
            root=Path(raw);engine=SessionEngine(root,"doctor",constrained=False);artifact,_=engine.artifact_store.put(b"durable",".bin");engine.close();engine=SessionEngine(root,"doctor",constrained=False)
            persisted=engine.resolve_artifact(artifact)==b"durable"
            call=ToolCall("probe","run_process",{"argv":[sys.executable,"-c","print('smara-process-ok')"],"cwd":".","timeout_seconds":10},str(root));result=engine.broker.dispatch(call);engine.close()
            checks["session_persistence"]=_entry(True,persisted,persisted,"content-addressed artifact reopen")
            process_ok=result.ok and "smara-process-ok" in result.text
            checks["process_operations"]=_entry(True,process_ok,True,f"exit_code={result.exit_code}")
    except Exception as exc:
        checks["session_persistence"]=_entry(True,False,False,type(exc).__name__);checks["process_operations"]=_entry(True,False,False,type(exc).__name__)
    from .cli import _load_local_profiles,_resolve_profile_key
    profiles,active_id,credentials=_load_local_profiles()
    active=next((item for item in profiles if item.get("id")==active_id),profiles[0])
    provider=bool(_resolve_profile_key(active,credentials))
    checks["model_provider"]=_entry(provider,provider,False,"configured credential present" if provider else "no configured credential")
    search=bool(os.getenv("TAVILY_API_KEY") or os.getenv("EXA_API_KEY"))
    if not search:
        try:
            from .desktop_executor import resolve_local_credential
            search=any(bool(resolve_local_credential(alias)) for alias in ("TAVILY_API_KEY","EXA_API_KEY","BRAVE_SEARCH_API_KEY","SERPER_API_KEY"))
        except Exception:
            search=False
    checks["search_provider"]=_entry(search,search,False,"configured provider present" if search else "no configured provider")
    pdf=importlib.util.find_spec("pypdf") is not None
    from .ocr_service import resolve_ocr_credentials
    _, ocr_key, _ = resolve_ocr_credentials()
    ocr_lib = importlib.util.find_spec("pytesseract") is not None
    ocr_available = bool(ocr_key) or ocr_lib
    ocr_detail = "Sarvam OCR API configured" if ocr_key else ("pytesseract import" if ocr_lib else "configure Sarvam key or install pytesseract")
    checks["pdf_extraction"]=_entry(True,pdf,pdf,"pypdf import")
    checks["ocr_extraction"]=_entry(bool(ocr_key),ocr_available,False,ocr_detail)
    browser_available=importlib.util.find_spec("playwright") is not None;browser_tested=False;detail="install smara[browser], then python -m playwright install chromium"
    if browser_available and test_browser:
        try:
            with tempfile.TemporaryDirectory(prefix="smara-browser-doctor-") as raw:
                from .managed_browser import ManagedBrowser
                backend=ManagedBrowser(Path(raw));ident=backend.create();observation=backend.observe(ident);browser_tested=observation["url"]=="about:blank";backend.close(ident);backend.shutdown();detail="fresh Chromium context observed"
        except Exception as exc:detail=f"{type(exc).__name__}: {exc}"
    checks["browser_backend"]=_entry(True,browser_available,browser_tested,detail)
    required=("workspace_writable","session_persistence","process_operations","pdf_extraction","browser_backend")
    from .autonomous_agent import get_tool_schemas
    from .harness import BUDGET_PROFILES
    def profile(name,budget):return {"id":name,"capabilities":[item["function"]["name"] for item in get_tool_schemas(name)],"model_context":{"accounting":"conservative_utf8_upper_bound","input_capacity":int(os.getenv("SMARA_MODEL_CONTEXT_TOKENS","65536")),"output_reserve":16384},"budget":BUDGET_PROFILES[budget].__dict__,"output_contract":"validated_result"}
    return {"version":1,"ok":all(checks[name]["tested"] for name in required),"checks":checks,"required":list(required),"profiles":{"research":profile("research","long"),"local_execution":profile("coding","coding")}}
