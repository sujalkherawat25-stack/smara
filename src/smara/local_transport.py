"""Authenticated local command surface over the canonical SessionEngine."""
from __future__ import annotations
import hmac
import json
from pathlib import Path
from typing import Any,Callable,Iterable
from .harness import SessionEngine
class EventGap(RuntimeError):pass
class LocalTransport:
    def __init__(self,token:str,allowed_roots:Iterable[Path]):
        if len(token)<32:raise ValueError("local transport token must contain at least 32 characters")
        self.token=token;self.roots=tuple(Path(root).resolve() for root in allowed_roots)
    def _authorize(self,token,workspace):
        if not hmac.compare_digest(self.token,str(token)):raise PermissionError("invalid local transport credential")
        root=Path(workspace).resolve()
        if not any(root==allowed or allowed in root.parents for allowed in self.roots):raise PermissionError("workspace is outside local transport scope")
        return root
    def inspect(self,token,workspace,session_id,after_sequence=0):
        root=self._authorize(token,workspace);engine=SessionEngine(root,session_id);record=engine.inspect();engine.close(); latest=record["events"][-1]["sequence"] if record["events"] else 0
        if after_sequence>latest:raise EventGap(f"cursor {after_sequence} is ahead of terminal sequence {latest}")
        record["events"]=[event for event in record["events"] if event["sequence"]>after_sequence];record["cursor"]=record["events"][-1]["sequence"] if record["events"] else after_sequence;record["envelope_version"]=1;return record
    def _command(self,engine:SessionEngine,command_id:str,kind:str,execute:Callable[[],dict[str,Any]]):
        commands=engine.get("transport_commands",{})
        if command_id in commands:
            if commands[command_id]["kind"]!=kind:raise ValueError("command ID was already used for another operation")
            return commands[command_id]["result"]
        result=json.loads(json.dumps(execute(),default=str));commands[command_id]={"kind":kind,"result":result};engine.set("transport_commands",commands);engine.event("transport_command",{"command_id":command_id,"kind":kind});return result
    def run(self,token,workspace,session_id,request,command_id,runner:Callable[[SessionEngine,str],dict[str,Any]]):
        root=self._authorize(token,workspace);engine=SessionEngine(root,session_id)
        try:return self._command(engine,command_id,"run",lambda:(engine.begin_incremental(str(request)) or runner(engine,str(request))))
        finally:engine.close()
    def resume(self,token,workspace,session_id,command_id,runner:Callable[[SessionEngine,str],dict[str,Any]]):
        root=self._authorize(token,workspace);engine=SessionEngine(root,session_id)
        try:
            request=engine.get("request")
            if request is None:raise ValueError("cannot resume an uninitialized session")
            return self._command(engine,command_id,"resume",lambda:runner(engine,request))
        finally:engine.close()
    def cancel(self,token,workspace,session_id,command_id="cancel"):
        root=self._authorize(token,workspace);engine=SessionEngine(root,session_id)
        try:
            def execute():engine.cancel();return engine.inspect()["state"].get("result") or {"status":"cancelled","session_id":session_id}
            return self._command(engine,command_id,"cancel",execute)
        finally:engine.close()
