"""Owned Playwright browser contexts with observation-bound actions."""
from __future__ import annotations
import hashlib, shutil, time, uuid
from dataclasses import dataclass,field
from pathlib import Path
from typing import Any
class BrowserUnavailable(RuntimeError):pass
class StaleObservation(RuntimeError):pass
@dataclass
class BrowserSession:
    id:str; context:Any; pages:dict[str,Any]=field(default_factory=dict); active_tab:str=""; generation:int=0; observations:dict[str,dict]=field(default_factory=dict); dialogs:list[dict]=field(default_factory=list); actions:list[dict]=field(default_factory=list)
class ManagedBrowser:
    def __init__(self,artifact_root:Path,executable_path:str|None=None,headless=True):
        self.root=Path(artifact_root); self.root.mkdir(parents=True,exist_ok=True); self.executable=executable_path or self._find(); self.headless=headless; self.runtime=None; self.browser=None; self.sessions={}
    @staticmethod
    def _find():
        candidates=[shutil.which("chrome"),shutil.which("msedge"),r"C:\Program Files\Google\Chrome\Application\chrome.exe",r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"]
        return next((str(p) for p in candidates if p and Path(p).is_file()),None)
    def start(self):
        if not self.executable:raise BrowserUnavailable("no supported Chromium executable")
        from playwright.sync_api import sync_playwright
        self.runtime=sync_playwright().start(); self.browser=self.runtime.chromium.launch(headless=self.headless,executable_path=self.executable)
    def create(self):
        if self.browser is None:self.start()
        ident=f"browser_{uuid.uuid4().hex[:20]}"; context=self.browser.new_context(accept_downloads=True); session=BrowserSession(ident,context)
        context.on("page",lambda page:self._attach(session,page)); context.on("dialog",lambda dialog:self._dialog(session,dialog)); page=context.new_page(); self._attach(session,page); self.sessions[ident]=session; return ident
    def _attach(self,session,page):
        if any(existing is page for existing in session.pages.values()):return
        tab=f"tab_{uuid.uuid4().hex[:16]}"; session.pages[tab]=page; session.active_tab=tab; session.generation+=1
    def _dialog(self,session,dialog):session.dialogs.append({"type":dialog.type,"message":dialog.message}); dialog.dismiss()
    def page(self,session):return session.pages[session.active_tab]
    def navigate(self,session_id,url):
        session=self.sessions[session_id]; self.page(session).goto(url,wait_until="domcontentloaded"); session.generation+=1; return self.observe(session_id)
    def observe(self,session_id):
        session=self.sessions[session_id]; page=self.page(session); session.generation+=1; refs={}; ref_index=0
        for frame_index,frame in enumerate(page.frames):
            elements=frame.locator("a,button,input,select,textarea,[role]")
            for index in range(min(elements.count(),200-len(refs))):
                locator=elements.nth(index); ref=f"e{ref_index}"; ref_index+=1; locator.evaluate("(el,ref)=>el.setAttribute('data-smara-ref',ref)",ref); refs[ref]={"selector":f'[data-smara-ref="{ref}"]',"frame_index":frame_index,"tag":locator.evaluate("el=>el.tagName.toLowerCase()"),"text":locator.inner_text()[:200] if locator.evaluate("el=>!['INPUT','TEXTAREA'].includes(el.tagName)") else ""}
        ident=f"obs_{uuid.uuid4().hex[:20]}"; screenshot=self.root/f"{ident}.png"; page.screenshot(path=str(screenshot),full_page=False)
        body_text=page.locator("body").inner_text()[:16000] if page.locator("body").count() else ""
        observation={"observation_id":ident,"session_id":session_id,"tab_id":session.active_tab,"url":page.url,"title":page.title(),"timestamp":time.time(),"generation":session.generation,"elements":refs,"body_text":body_text,"screenshot":str(screenshot),"screenshot_sha256":hashlib.sha256(screenshot.read_bytes()).hexdigest()}; session.observations[ident]=observation; return observation
    def _ground(self,session,observation,ref):
        if ref not in observation["elements"]:raise ValueError("unknown observed element")
        item=observation["elements"][ref]; frames=self.page(session).frames
        if item["frame_index"]>=len(frames):raise StaleObservation("observed frame no longer exists")
        return frames[item["frame_index"]].locator(item["selector"])
    def act(self,session_id,observation_id,ref,action,value=None):
        session=self.sessions[session_id]; observation=session.observations.get(observation_id)
        if not observation or observation["generation"]!=session.generation or observation["tab_id"]!=session.active_tab:raise StaleObservation("browser observation is stale")
        locator=self._ground(session,observation,ref); attempt={"observation_id":observation_id,"ref":ref,"action":action,"started_at":time.time(),"status":"attempted"}; session.actions.append(attempt)
        try:
            if action=="click":locator.click()
            elif action=="fill":locator.fill(str(value or ""))
            elif action=="select":locator.select_option(str(value))
            elif action=="check":locator.check()
            elif action=="upload":locator.set_input_files(str(value))
            else:raise ValueError("unsupported browser action")
        except Exception as exc:
            attempt.update(status="failed",error=type(exc).__name__,finished_at=time.time()); raise
        attempt.update(status="completed",finished_at=time.time())
        session.generation+=1; return self.observe(session_id)
    def wait_for(self,session_id,selector,timeout_ms=5000):
        session=self.sessions[session_id]; self.page(session).locator(selector).wait_for(state="visible",timeout=timeout_ms); session.generation+=1; return self.observe(session_id)
    def download(self,session_id,observation_id,ref):
        session=self.sessions[session_id]; observation=session.observations.get(observation_id)
        if not observation or observation["generation"]!=session.generation:raise StaleObservation("browser observation is stale")
        locator=self._ground(session,observation,ref)
        with self.page(session).expect_download() as pending: locator.click()
        download=pending.value; target=self.root/(uuid.uuid4().hex+"-"+download.suggested_filename); download.save_as(str(target)); session.generation+=1
        return {"path":str(target),"sha256":hashlib.sha256(target.read_bytes()).hexdigest(),"suggested_filename":download.suggested_filename,"observation":self.observe(session_id)}
    def scroll(self,session_id,dy=600):
        session=self.sessions[session_id]; self.page(session).mouse.wheel(0,dy); session.generation+=1; return self.observe(session_id)
    def tabs(self,session_id):return [{"tab_id":key,"url":page.url} for key,page in self.sessions[session_id].pages.items()]
    def switch(self,session_id,tab_id):
        session=self.sessions[session_id]
        if tab_id not in session.pages:raise KeyError(tab_id)
        session.active_tab=tab_id; session.generation+=1; return self.observe(session_id)
    def close(self,session_id):self.sessions.pop(session_id).context.close()
    def cancel(self,session_id): self.close(session_id)
    def invalidate_after_restart(self):
        invalidated=list(self.sessions)
        for ident in invalidated:self.close(ident)
        return invalidated
    def shutdown(self):
        for ident in list(self.sessions):self.close(ident)
        if self.browser:self.browser.close()
        if self.runtime:self.runtime.stop()
