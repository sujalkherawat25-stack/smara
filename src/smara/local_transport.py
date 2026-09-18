"""Authenticated local command surface over the canonical SessionEngine."""
from __future__ import annotations
import hmac
import json
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import threading
from typing import Any,Callable,Iterable
from urllib.parse import parse_qs,urlparse
from .harness import SessionEngine
from .runtime_session import session_store_for_workspace
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
        record["events"]=[event for event in record["events"] if event["sequence"]>after_sequence];record["cursor"]=record["events"][-1]["sequence"] if record["events"] else after_sequence;record["envelope_version"]=1
        runtime=session_store_for_workspace(root)
        if runtime.get(session_id) is not None:
            record["runtime"]=runtime.snapshot(session_id,after=after_sequence)
            record["event_cursor"]=record["runtime"]["cursor"]
        return record
    def _command(self,engine:SessionEngine,command_id:str,kind:str,execute:Callable[[],dict[str,Any]]):
        if not command_id:raise ValueError("command_id is required")
        commands=engine.get("transport_commands",{})
        if command_id in commands:
            if commands[command_id]["kind"]!=kind:raise ValueError("command ID was already used for another operation")
            return commands[command_id]["result"]
        result=json.loads(json.dumps(execute(),default=str));commands[command_id]={"kind":kind,"result":result};engine.set("transport_commands",commands);engine.event("transport_command",{"command_id":command_id,"kind":kind});return result
    def run(self,token,workspace,session_id,request,command_id,runner:Callable[[SessionEngine,str],dict[str,Any]]):
        root=self._authorize(token,workspace);engine=SessionEngine(root,session_id);runtime=session_store_for_workspace(root)
        runtime.create_or_get(session_id,request=str(request),workspace_id=str(root),mode="loopback")
        already_command = bool(command_id and command_id in engine.get("transport_commands",{}))
        if not already_command:
            runtime.checkpoint(session_id,status="running",event="turn.started",event_payload={"request":str(request)[:500]})
        try:
            result=self._command(engine,command_id,"run",lambda:(engine.begin_incremental(str(request)) or runner(engine,str(request))))
            if not already_command:
                status=str(result.get("status") or engine.inspect().get("state",{}).get("result",{}).get("status") or "needs_input")
                runtime.checkpoint(session_id,status=status,result=result,unresolved=result.get("unresolved_items") or [],event=f"turn.{status}")
            return {**result,"runtime":runtime.snapshot(session_id),"event_cursor":runtime.get(session_id).revision}
        finally:engine.close()
    def resume(self,token,workspace,session_id,command_id,runner:Callable[[SessionEngine,str],dict[str,Any]]):
        root=self._authorize(token,workspace);engine=SessionEngine(root,session_id);runtime=session_store_for_workspace(root)
        try:
            request=engine.get("request")
            if request is None:raise ValueError("cannot resume an uninitialized session")
            runtime.resume(session_id)
            result=self._command(engine,command_id,"resume",lambda:runner(engine,request))
            status=str(result.get("status") or "needs_input")
            runtime.checkpoint(session_id,status=status,result=result,unresolved=result.get("unresolved_items") or [],event=f"turn.{status}")
            return {**result,"runtime":runtime.snapshot(session_id),"event_cursor":runtime.get(session_id).revision}
        finally:engine.close()
    def cancel(self,token,workspace,session_id,command_id="cancel"):
        root=self._authorize(token,workspace);engine=SessionEngine(root,session_id);runtime=session_store_for_workspace(root)
        try:
            runtime.create_or_get(session_id,request=str(engine.get("request") or ""),workspace_id=str(root),mode="loopback")
            already_command = bool(command_id and command_id in engine.get("transport_commands",{}))
            if not already_command:
                runtime.request_cancel(session_id)
            def execute():engine.cancel();return engine.inspect()["state"].get("result") or {"status":"cancelled","session_id":session_id}
            result=self._command(engine,command_id,"cancel",execute)
            return {**result,"runtime":runtime.snapshot(session_id),"event_cursor":runtime.get(session_id).revision}
        finally:engine.close()

class LocalTransportServer:
    """Loopback-only versioned JSON adapter for desktop/mobile clients."""
    def __init__(self,transport:LocalTransport,runner:Callable[[SessionEngine,str],dict[str,Any]]):
        self.transport=transport;self.runner=runner;self.server=None;self.thread=None
    def start(self)->str:
        owner=self
        class Handler(BaseHTTPRequestHandler):
            server_version="SmaraLocalTransport/1"
            def log_message(self,*_):return
            def reply(self,status,payload):
                data=json.dumps(payload,default=str).encode();self.send_response(status);self.send_header("Content-Type","application/json");self.send_header("Content-Length",str(len(data)));self.end_headers();self.wfile.write(data)
            def token(self):
                value=self.headers.get("Authorization","");return value[7:] if value.startswith("Bearer ") else ""
            def route(self):
                parsed=urlparse(self.path);parts=[part for part in parsed.path.split("/") if part]
                if len(parts)<3 or parts[:2]!=["v1","sessions"]:raise ValueError("unknown route")
                return parsed,parts[2],parts[3] if len(parts)>3 else "inspect"
            def body(self):
                size=int(self.headers.get("Content-Length","0"));value=json.loads(self.rfile.read(size) or b"{}")
                if not isinstance(value,dict):raise ValueError("JSON body must be an object")
                return value
            def dispatch(self,method):
                try:
                    parsed,session_id,operation=self.route();body=self.body() if method=="POST" else {};query=parse_qs(parsed.query);workspace=body.get("workspace") or (query.get("workspace") or [""])[0]
                    if operation=="inspect" and method=="GET":result=owner.transport.inspect(self.token(),workspace,session_id,int((query.get("after") or [0])[0]))
                    elif operation=="run" and method=="POST":result=owner.transport.run(self.token(),workspace,session_id,body.get("request",""),str(body.get("command_id") or ""),owner.runner)
                    elif operation=="resume" and method=="POST":result=owner.transport.resume(self.token(),workspace,session_id,str(body.get("command_id") or ""),owner.runner)
                    elif operation=="cancel" and method=="POST":result=owner.transport.cancel(self.token(),workspace,session_id,str(body.get("command_id") or "cancel"))
                    else:raise ValueError("method is not supported for route")
                    self.reply(200,{"version":1,"session_id":session_id,"result":result})
                except PermissionError as exc:self.reply(403,{"version":1,"error":str(exc)})
                except EventGap as exc:self.reply(409,{"version":1,"error":str(exc)})
                except (ValueError,KeyError,json.JSONDecodeError) as exc:self.reply(400,{"version":1,"error":str(exc)})
            def do_GET(self):self.dispatch("GET")
            def do_POST(self):self.dispatch("POST")
        self.server=ThreadingHTTPServer(("127.0.0.1",0),Handler);self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();host,port=self.server.server_address;return f"http://{host}:{port}"
    def close(self):
        if self.server:self.server.shutdown();self.server.server_close()
        if self.thread:self.thread.join(5)
