"""Canonical session-owned adapter for the managed Playwright backend."""
from __future__ import annotations

import contextlib
import hashlib
import os
import tempfile
import threading
from pathlib import Path
from typing import Any
from urllib.parse import unquote,urlparse

from .managed_browser import ManagedBrowser


class BrowserPolicyError(PermissionError):pass


class CanonicalBrowserSession:
    def __init__(self,workspace:Path,*,session_engine=None,backend:ManagedBrowser|None=None):
        self.workspace=Path(workspace).resolve();self.engine=session_engine;self.owner_thread=threading.get_ident()
        root=(session_engine.root/session_engine.session_id/"browser") if session_engine is not None else (self.workspace/".smara"/"browser")
        self.backend=backend or ManagedBrowser(root);self.browser_session_id=None
        if self.engine is not None:
            old=self.engine.get("browser_handle")
            if old:
                self.engine.event("browser_invalidated",{"reason":"backend_process_reconstructed","previous_handle":old});self.engine.set("browser_handle",None)
            self.engine.register_canceller(self.cancel)

    def _owner(self):
        if threading.get_ident()!=self.owner_thread:raise RuntimeError("browser action must run on its stable owner thread")

    def _require(self):
        self._owner()
        if not self.browser_session_id or self.browser_session_id not in self.backend.sessions:raise RuntimeError("browser session is not open")
        return self.browser_session_id

    def _safe_url(self,url:str) -> str:
        parsed=urlparse(str(url))
        if parsed.scheme in {"http","https","about","data"}:return str(url)
        if parsed.scheme=="file":
            candidate=Path(unquote(parsed.path.lstrip("/") if os.name=="nt" else parsed.path)).resolve()
            if candidate!=self.workspace and self.workspace not in candidate.parents:raise BrowserPolicyError("file URL escapes workspace")
            return candidate.as_uri()
        raise BrowserPolicyError("browser URL scheme is not allowed")

    def _workspace_path(self,raw:str) -> Path:
        candidate=Path(raw);resolved=(candidate if candidate.is_absolute() else self.workspace/candidate).resolve()
        if resolved!=self.workspace and self.workspace not in resolved.parents:raise BrowserPolicyError("browser file path escapes workspace")
        return resolved

    def _persist_observation(self,observation:dict[str,Any]) -> dict[str,Any]:
        value=dict(observation);screenshot=Path(value.pop("screenshot"))
        if self.engine is not None:
            data=screenshot.read_bytes();artifact_id,_=self.engine.artifact_store.put(data,".png")
            if hashlib.sha256(data).hexdigest()!=value.get("screenshot_sha256"):raise RuntimeError("browser screenshot hash mismatch")
            value["screenshot_artifact_id"]=artifact_id
        else:value["screenshot_path"]=str(screenshot)
        return value

    def open(self,url:str="about:blank") -> dict[str,Any]:
        self._owner()
        if self.browser_session_id:self.close()
        self.browser_session_id=self.backend.create()
        if self.engine is not None:self.engine.set("browser_handle",{"kind":"browser","browser_session_id":self.browser_session_id,"owner_session_id":self.engine.session_id})
        observation=self.backend.navigate(self.browser_session_id,self._safe_url(url)) if url!="about:blank" else self.backend.observe(self.browser_session_id)
        return {"status":"ok","observation":self._persist_observation(observation)}

    def observe(self) -> dict[str,Any]:return {"status":"ok","observation":self._persist_observation(self.backend.observe(self._require()))}

    def navigate(self,url:str) -> dict[str,Any]:return {"status":"ok","observation":self._persist_observation(self.backend.navigate(self._require(),self._safe_url(url)))}

    def act(self,observation_id:str,ref:str,action:str,value:Any=None) -> dict[str,Any]:
        if action=="upload":value=str(self._workspace_path(str(value)))
        observation=self.backend.act(self._require(),observation_id,ref,action,value)
        return {"status":"ok","action":{"observation_id":observation_id,"ref":ref,"action":action},"observation":self._persist_observation(observation)}

    def tabs(self) -> dict[str,Any]:return {"status":"ok","tabs":self.backend.tabs(self._require())}
    def switch(self,tab_id:str) -> dict[str,Any]:return {"status":"ok","observation":self._persist_observation(self.backend.switch(self._require(),tab_id))}
    def scroll(self,dy:int=600) -> dict[str,Any]:return {"status":"ok","observation":self._persist_observation(self.backend.scroll(self._require(),max(-10000,min(int(dy),10000))))}

    def download(self,observation_id:str,ref:str,destination:str) -> dict[str,Any]:
        receipt=self.backend.download(self._require(),observation_id,ref);source=Path(receipt["path"]);target=self._workspace_path(destination)
        target.parent.mkdir(parents=True,exist_ok=True);fd,tmp=tempfile.mkstemp(prefix=f".{target.name}.",dir=target.parent)
        try:
            with os.fdopen(fd,"wb") as out:out.write(source.read_bytes());out.flush();os.fsync(out.fileno())
            os.replace(tmp,target)
        finally:
            with contextlib.suppress(OSError):os.unlink(tmp)
        return {"status":"ok","destination":target.relative_to(self.workspace).as_posix(),"sha256":hashlib.sha256(target.read_bytes()).hexdigest(),"suggested_filename":receipt["suggested_filename"],"observation":self._persist_observation(receipt["observation"])}

    def close(self) -> dict[str,Any]:
        self._owner();ident=self.browser_session_id
        if ident and ident in self.backend.sessions:self.backend.close(ident)
        self.browser_session_id=None
        if self.engine is not None:self.engine.set("browser_handle",None)
        return {"status":"ok","closed":bool(ident)}

    def cancel(self) -> None:
        # Session cancellation is synchronous and normally occurs on the owner
        # thread. Cross-thread cancellation remains fail-safe by closing the
        # underlying context directly.
        ident=self.browser_session_id
        if ident and ident in self.backend.sessions:
            with contextlib.suppress(Exception):self.backend.cancel(ident)
        self.browser_session_id=None
        if self.engine is not None:self.engine.set("browser_handle",None);self.engine.event("browser_cancelled",{})

    def shutdown(self) -> None:
        self.cancel()
        with contextlib.suppress(Exception):self.backend.shutdown()
