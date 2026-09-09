"""Durable, brokered execution spine shared by CLI and benchmarks."""
from __future__ import annotations

import contextlib, hashlib, json, os, re, signal, sqlite3, subprocess, tempfile, threading, time, uuid
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

RUN_STATUSES = {"completed", "needs_input", "denied", "cancelled", "budget_exhausted", "provider_error", "tool_error", "interrupted"}
MUTATING_TOOLS = {"write_file", "patch_file", "run_process", "process_start", "process_write"}
VERIFY_SCOPES = {"syntax", "focused", "full"}
REUSABLE_READ_TOOLS = {"read_file", "file_read", "list_directory", "search_files", "code_graph", "pdf_search", "skills_list", "skill_view"}
_LIVE_PROCESSES: dict[str, subprocess.Popen] = {}
_LIVE_PROCESS_LOGS: dict[str, Any] = {}
_LIVE_PROCESS_JOBS: dict[str, Any] = {}

def _now(): return datetime.now(timezone.utc).isoformat()
def _json(value): return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
def _sha(data: bytes): return hashlib.sha256(data).hexdigest()
def _file_sha(path: Path):
    try: return _sha(path.read_bytes())
    except OSError: return None

def workspace_revision(root: Path) -> str:
    digest = hashlib.sha256(); ignored = {".git", ".smara", "__pycache__", ".pytest_cache", "node_modules"}
    try:
        files = sorted(p for p in root.rglob("*") if p.is_file() and not any(x in ignored for x in p.relative_to(root).parts))
        for path in files:
            rel = path.relative_to(root).as_posix().encode(); digest.update(len(rel).to_bytes(4, "big")); digest.update(rel)
            value = _file_sha(path)
            if value: digest.update(bytes.fromhex(value))
    except OSError: pass
    return digest.hexdigest()

@dataclass(frozen=True)
class Budget:
    wall_seconds: float = 30.0
    tool_calls: int = 4
    model_calls: int = 4
    billed_tokens: int = 20_000
    dollars: float = .10
    def __post_init__(self):
        if self.wall_seconds <= 0 or min(self.tool_calls, self.model_calls, self.billed_tokens) < 0 or self.dollars < 0: raise ValueError("invalid budget")

BUDGET_PROFILES = {"short": Budget(), "gaia": Budget(900,160,80,400_000,3), "coding": Budget(1800,240,120,800_000,5), "long": Budget(5400,600,300,2_000_000,15)}

@dataclass(frozen=True)
class RunRequest:
    session_id: str; workspace_id: str; user_message: str; model_profile: str = "default"
    capability_grant: tuple[str,...] = (); budget: Budget = field(default_factory=Budget)
    attachments: tuple[str,...] = (); output_contract: Mapping[str,Any] = field(default_factory=dict)

@dataclass(frozen=True)
class ToolCall:
    call_id: str; name: str; arguments: Mapping[str,Any]; workspace_id: str; observation_id: str|None = None

@dataclass(frozen=True)
class ToolResult:
    call_id: str; status: str; text: str = ""; artifacts: tuple[str,...] = (); exit_code: int|None = None
    duration_ms: int = 0; changed_paths: tuple[str,...] = (); before_revision: str|None = None
    after_revision: str|None = None; error_kind: str|None = None; meta: Mapping[str,Any] = field(default_factory=dict)
    @property
    def ok(self): return self.status == "ok" and self.exit_code in (None, 0)

@dataclass(frozen=True)
class Evidence:
    id: str; call_id: str; kind: str; scope: str; subject_revision: str; passed: bool; source_artifact_id: str|None; created_at: str

@dataclass(frozen=True)
class RunEvent:
    version: int; session_id: str; sequence: int; type: str; timestamp: str; payload: Mapping[str,Any]

@dataclass(frozen=True)
class RunResult:
    status: str; answer: str; artifacts: tuple[str,...]; verification: tuple[Mapping[str,Any],...]; usage: Mapping[str,Any]
    unresolved_items: tuple[str,...]; resume_token: str; session_id: str; engine_version: str
    def to_dict(self): return asdict(self)

class SchemaError(ValueError): pass
class PolicyDenied(PermissionError): pass
class BudgetExceeded(RuntimeError): pass
class SessionBusy(RuntimeError): pass

class _SessionLock:
    def __init__(self, path): self.path, self.handle = path, None
    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True); self.handle = self.path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt
                self.handle.seek(0); self.handle.write(b"0"); self.handle.flush(); self.handle.seek(0); msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError as exc:
            self.handle.close(); raise SessionBusy("session already has an active writer") from exc
        return self
    def __exit__(self, *_):
        with contextlib.suppress(OSError):
            if os.name == "nt":
                import msvcrt
                self.handle.seek(0); msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        self.handle.close()

class ArtifactStore:
    def __init__(self, root): self.root=Path(root); self.root.mkdir(parents=True,exist_ok=True)
    def put(self, data: bytes, suffix=".bin"):
        ident=_sha(data); existing=sorted(self.root.glob(f"{ident}.*"))
        if existing:
            valid=[item for item in existing if _file_sha(item)==ident]
            if valid:return ident,valid[0]
            raise ValueError("artifact hash collision or corruption")
        path=self.root/f"{ident}{suffix}"
        if not path.exists():
            fd,tmp=tempfile.mkstemp(prefix="artifact-",dir=self.root)
            try:
                with os.fdopen(fd,"wb") as out: out.write(data); out.flush(); os.fsync(out.fileno())
                os.replace(tmp,path)
            finally:
                with contextlib.suppress(OSError): os.unlink(tmp)
        return ident,path
    def put_json(self,value): return self.put(_json(value).encode(),".json")

def _validate(value, schema, path="arguments"):
    kind=schema.get("type")
    checks={"object":lambda:isinstance(value,dict),"array":lambda:isinstance(value,list),"string":lambda:isinstance(value,str),"integer":lambda:isinstance(value,int) and not isinstance(value,bool),"number":lambda:isinstance(value,(int,float)) and not isinstance(value,bool),"boolean":lambda:isinstance(value,bool)}
    if kind in checks and not checks[kind](): raise SchemaError(f"{path} must be {kind}")
    if "enum" in schema and value not in schema["enum"]: raise SchemaError(f"{path} is not allowed")
    if isinstance(value,str) and not int(schema.get("minLength",0)) <= len(value) <= int(schema.get("maxLength",2**31)): raise SchemaError(f"{path} has invalid length")
    if isinstance(value,list):
        if len(value)>int(schema.get("maxItems",2**31)): raise SchemaError(f"{path} has too many items")
        for i,item in enumerate(value): _validate(item,schema.get("items",{}),f"{path}[{i}]")
    if isinstance(value,dict):
        props=schema.get("properties",{}); missing=set(schema.get("required",()))-set(value)
        if missing: raise SchemaError(f"{path} missing: {', '.join(sorted(missing))}")
        extra=set(value)-set(props)
        if schema.get("additionalProperties") is False and extra: raise SchemaError(f"{path} unknown: {', '.join(sorted(extra))}")
        for key,item in value.items():
            if key in props: _validate(item,props[key],f"{path}.{key}")

TOOL_SCHEMAS={
 "read_file":{"type":"object","additionalProperties":False,"required":["path"],"properties":{"path":{"type":"string","minLength":1},"max_chars":{"type":"integer"}}},
 "write_file":{"type":"object","additionalProperties":False,"required":["path","content"],"properties":{"path":{"type":"string","minLength":1},"content":{"type":"string"},"expected_sha256":{"type":"string"}}},
 "patch_file":{"type":"object","additionalProperties":False,"required":["path","old","new"],"properties":{"path":{"type":"string"},"old":{"type":"string","minLength":1},"new":{"type":"string"},"expected_sha256":{"type":"string"},"replace_all":{"type":"boolean"}}},
 "run_process":{"type":"object","additionalProperties":False,"required":["argv"],"properties":{"argv":{"type":"array","maxItems":64,"items":{"type":"string","minLength":1,"maxLength":4000}},"cwd":{"type":"string"},"timeout_seconds":{"type":"number"},"evidence_scope":{"type":"string","enum":["none","syntax","focused","full"]},"env":{"type":"object"}}},
 "process_start":{"type":"object","additionalProperties":False,"required":["argv"],"properties":{"argv":{"type":"array","maxItems":64,"items":{"type":"string","minLength":1}},"cwd":{"type":"string"},"timeout_seconds":{"type":"number"},"env":{"type":"object"}}},
 "process_poll":{"type":"object","additionalProperties":False,"required":["process_id"],"properties":{"process_id":{"type":"string"}}},
 "process_write":{"type":"object","additionalProperties":False,"required":["process_id","text"],"properties":{"process_id":{"type":"string"},"text":{"type":"string"}}},
 "process_cancel":{"type":"object","additionalProperties":False,"required":["process_id"],"properties":{"process_id":{"type":"string"}}},
 "browser_open":{"type":"object"},"browser_observe":{"type":"object"},"browser_navigate":{"type":"object"},"browser_act":{"type":"object"},"browser_tabs":{"type":"object"},"browser_switch":{"type":"object"},"browser_scroll":{"type":"object"},"browser_download":{"type":"object"},"browser_close":{"type":"object"},
 "research_plan":{"type":"object"},"research_search":{"type":"object"},"research_fetch":{"type":"object"},"research_ingest_file":{"type":"object"},"research_inspect":{"type":"object"},"research_resolve":{"type":"object"},"research_validate":{"type":"object"},
}

class ProcessSupervisor:
    def __init__(self,root): self.root=Path(root); self.root.mkdir(parents=True,exist_ok=True); self.processes=_LIVE_PROCESSES; self.logs=_LIVE_PROCESS_LOGS; self.jobs=_LIVE_PROCESS_JOBS; self.lock=threading.RLock()
    @staticmethod
    def creation(): return ((getattr(subprocess,"CREATE_NEW_PROCESS_GROUP",0)|getattr(subprocess,"CREATE_NO_WINDOW",0),None) if os.name=="nt" else (0,os.setsid))
    def start(self,argv,cwd,env,timeout):
        ident=f"proc_{uuid.uuid4().hex[:24]}"; log=self.root/f"{ident}.log"; handle=log.open("w+b"); flags,preexec=self.creation()
        proc=subprocess.Popen(argv,cwd=str(cwd),env=env,stdin=subprocess.PIPE,stdout=handle,stderr=subprocess.STDOUT,shell=False,creationflags=flags,preexec_fn=preexec)
        if os.name=="nt":
            job=self._windows_job(proc)
            if job:self.jobs[ident]=job
        self.processes[ident]=proc; self.logs[ident]=handle
        meta={"process_id":ident,"pid":proc.pid,"cwd":str(cwd),"argv":argv,"started_at":_now(),"timeout_seconds":timeout,"log_path":str(log)}
        (self.root/f"{ident}.json").write_text(_json(meta),encoding="utf-8"); return meta
    def poll(self,ident):
        meta_path=self.root/f"{ident}.json"
        if not meta_path.exists(): raise KeyError(ident)
        meta=json.loads(meta_path.read_text()); proc=self.processes.get(ident); log=Path(meta["log_path"])
        if proc is None: return {**meta,"status":"lost","done":True,"exit_code":None,"output":log.read_text(errors="replace")[-16000:] if log.exists() else ""}
        code=proc.poll()
        if code is not None: self.logs[ident].flush()
        return {**meta,"status":"running" if code is None else "completed" if code==0 else "failed","done":code is not None,"exit_code":code,"output":log.read_text(errors="replace")[-16000:] if log.exists() else ""}
    def write(self,ident,text):
        proc=self.processes.get(ident)
        if proc is None or proc.poll() is not None or proc.stdin is None: raise RuntimeError("process is not running")
        proc.stdin.write(text.encode()); proc.stdin.flush(); return self.poll(ident)
    @staticmethod
    def kill(proc):
        if proc.poll() is not None:return
        if os.name=="nt": subprocess.run(["taskkill","/PID",str(proc.pid),"/T","/F"],capture_output=True,check=False)
        else:
            with contextlib.suppress(OSError): os.killpg(proc.pid,signal.SIGTERM)
        with contextlib.suppress(subprocess.TimeoutExpired): proc.wait(timeout=5)
        if proc.poll() is None: proc.kill()
    def cancel(self,ident):
        proc=self.processes.get(ident)
        if proc is None: return {**self.poll(ident),"status":"cancelled"}
        job=self.jobs.pop(ident,None)
        if job:
            import ctypes
            ctypes.windll.kernel32.CloseHandle(job)
            with contextlib.suppress(subprocess.TimeoutExpired): proc.wait(timeout=5)
        else:self.kill(proc)
        return {**self.poll(ident),"status":"cancelled","done":True}
    @staticmethod
    def _windows_job(proc):
        """Put the process in a kill-on-close Job Object (descendants inherit it)."""
        import ctypes
        from ctypes import wintypes
        class BASIC(ctypes.Structure):
            _fields_=[("PerProcessUserTimeLimit",ctypes.c_longlong),("PerJobUserTimeLimit",ctypes.c_longlong),("LimitFlags",wintypes.DWORD),("MinimumWorkingSetSize",ctypes.c_size_t),("MaximumWorkingSetSize",ctypes.c_size_t),("ActiveProcessLimit",wintypes.DWORD),("Affinity",ctypes.c_size_t),("PriorityClass",wintypes.DWORD),("SchedulingClass",wintypes.DWORD)]
        class IO(ctypes.Structure):
            _fields_=[("ReadOperationCount",ctypes.c_ulonglong),("WriteOperationCount",ctypes.c_ulonglong),("OtherOperationCount",ctypes.c_ulonglong),("ReadTransferCount",ctypes.c_ulonglong),("WriteTransferCount",ctypes.c_ulonglong),("OtherTransferCount",ctypes.c_ulonglong)]
        class EXTENDED(ctypes.Structure):
            _fields_=[("BasicLimitInformation",BASIC),("IoInfo",IO),("ProcessMemoryLimit",ctypes.c_size_t),("JobMemoryLimit",ctypes.c_size_t),("PeakProcessMemoryUsed",ctypes.c_size_t),("PeakJobMemoryUsed",ctypes.c_size_t)]
        kernel=ctypes.windll.kernel32
        kernel.CreateJobObjectW.restype=wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes=[wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD]
        kernel.AssignProcessToJobObject.argtypes=[wintypes.HANDLE,wintypes.HANDLE]
        kernel.CloseHandle.argtypes=[wintypes.HANDLE]
        job=kernel.CreateJobObjectW(None,None)
        if not job:return None
        info=EXTENDED(); info.BasicLimitInformation.LimitFlags=0x2000
        if not kernel.SetInformationJobObject(job,9,ctypes.byref(info),ctypes.sizeof(info)) or not kernel.AssignProcessToJobObject(job,wintypes.HANDLE(proc._handle)):
            kernel.CloseHandle(job); return None
        return job

class ToolBroker:
    def __init__(self,workspace,capability_grant,*,constrained=True,allowed_env=("PATH","SYSTEMROOT","WINDIR","TEMP","TMP","PATHEXT"),process_root=None):
        self.workspace=Path(workspace).resolve(strict=True); self.grant=frozenset(capability_grant); self.constrained=constrained; self.allowed_env=frozenset(x.upper() for x in allowed_env)
        self.processes=ProcessSupervisor(process_root or self.workspace/".smara"/"processes"); self.mutation_lock=threading.RLock()
    def path(self,raw,mutation=False):
        candidate=Path(raw); candidate=candidate if candidate.is_absolute() else self.workspace/candidate; resolved=candidate.resolve(strict=False)
        if resolved!=self.workspace and self.workspace not in resolved.parents: raise PolicyDenied("path escapes workspace")
        ancestor=resolved if resolved.exists() else next((p for p in resolved.parents if p.exists()),None)
        if ancestor is None or (ancestor.resolve()!=self.workspace and self.workspace not in ancestor.resolve().parents): raise PolicyDenied("path ancestor escapes workspace")
        if mutation and candidate.resolve(strict=False)!=resolved: raise PolicyDenied("path changed during admission")
        return resolved
    def env(self,supplied):
        result={k:v for k,v in os.environ.items() if k.upper() in self.allowed_env}
        for k,v in dict(supplied or {}).items():
            if k.upper() not in self.allowed_env: raise PolicyDenied(f"environment variable denied: {k}")
            if not isinstance(v,str): raise SchemaError(f"environment value {k} must be string")
            result[k]=v
        return result
    def dispatch(self,call):
        started=time.monotonic(); before=workspace_revision(self.workspace)
        if call.workspace_id!=str(self.workspace): return ToolResult(call.call_id,"denied",error_kind="workspace_mismatch",before_revision=before,after_revision=before)
        if call.name not in self.grant: return ToolResult(call.call_id,"denied",error_kind="capability_denied",before_revision=before,after_revision=before)
        if call.name not in TOOL_SCHEMAS: return ToolResult(call.call_id,"denied",error_kind="unknown_tool",before_revision=before,after_revision=before)
        try:
            _validate(dict(call.arguments),TOOL_SCHEMAS[call.name]); lock=self.mutation_lock if call.name in MUTATING_TOOLS else contextlib.nullcontext()
            with lock: payload=getattr(self,f"do_{call.name}")(dict(call.arguments))
            status=payload.pop("status","ok"); text=payload.pop("text","")
            return ToolResult(call.call_id,status,text,duration_ms=int((time.monotonic()-started)*1000),before_revision=before,after_revision=workspace_revision(self.workspace),**payload)
        except SchemaError as exc: return ToolResult(call.call_id,"schema_error",str(exc),error_kind="schema_error",before_revision=before,after_revision=workspace_revision(self.workspace))
        except PolicyDenied as exc: return ToolResult(call.call_id,"denied",str(exc),error_kind="policy_denied",before_revision=before,after_revision=workspace_revision(self.workspace))
        except subprocess.TimeoutExpired as exc: return ToolResult(call.call_id,"error",f"timeout after {exc.timeout}s",error_kind="timeout",before_revision=before,after_revision=workspace_revision(self.workspace))
        except Exception as exc: return ToolResult(call.call_id,"error",f"{type(exc).__name__}: {exc}",error_kind=type(exc).__name__,before_revision=before,after_revision=workspace_revision(self.workspace))
    def do_read_file(self,a):
        path=self.path(a["path"]); return {"text":path.read_text(encoding="utf-8",errors="replace")[:max(1,min(int(a.get("max_chars",16000)),1_000_000))],"meta":{"path":path.relative_to(self.workspace).as_posix(),"sha256":_file_sha(path)}}
    def atomic(self,path,content,expected):
        path=self.path(path,True); current=_file_sha(path)
        if expected is not None and current!=expected: raise PolicyDenied("expected-content hash mismatch")
        path.parent.mkdir(parents=True,exist_ok=True); fd,tmp=tempfile.mkstemp(prefix=f".{path.name}.",dir=path.parent)
        try:
            with os.fdopen(fd,"w",encoding="utf-8",newline="") as out: out.write(content); out.flush(); os.fsync(out.fileno())
            self.path(path,True); os.replace(tmp,path)
        finally:
            with contextlib.suppress(OSError): os.unlink(tmp)
        rel=path.relative_to(self.workspace).as_posix(); return {"text":f"wrote {rel}","changed_paths":(rel,),"meta":{"sha256":_file_sha(path)}}
    def do_write_file(self,a): return self.atomic(a["path"],a["content"],a.get("expected_sha256"))
    def do_patch_file(self,a):
        path=self.path(a["path"],True); old=path.read_text(encoding="utf-8"); count=old.count(a["old"])
        if not count: raise ValueError("expected text not found")
        if count>1 and not a.get("replace_all",False): raise ValueError("expected text not unique")
        return self.atomic(path,old.replace(a["old"],a["new"],-1 if a.get("replace_all") else 1),a.get("expected_sha256"))
    def process_args(self,a):
        if self.constrained: raise PolicyDenied("terminal needs explicit unrestricted-local grant or external sandbox")
        cwd=self.path(a.get("cwd","."));
        if not cwd.is_dir(): raise SchemaError("cwd must be directory")
        return list(a["argv"]),cwd,self.env(a.get("env")),max(.1,min(float(a.get("timeout_seconds",45)),3600))
    def do_run_process(self,a):
        argv,cwd,env,timeout=self.process_args(a); flags,preexec=ProcessSupervisor.creation(); proc=subprocess.Popen(argv,cwd=str(cwd),env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,stdin=subprocess.DEVNULL,shell=False,creationflags=flags,preexec_fn=preexec)
        try: output=proc.communicate(timeout=timeout)[0].decode(errors="replace")
        except subprocess.TimeoutExpired: ProcessSupervisor.kill(proc); raise
        return {"status":"ok" if proc.returncode==0 else "error","text":output[-16000:],"exit_code":proc.returncode,"error_kind":None if proc.returncode==0 else "nonzero_exit","meta":{"evidence_scope":a.get("evidence_scope","none")}}
    def do_process_start(self,a):
        argv,cwd,env,timeout=self.process_args(a); meta=self.processes.start(argv,cwd,env,timeout); return {"text":_json(meta),"meta":meta}
    def do_process_poll(self,a):
        state=self.processes.poll(a["process_id"]); return {"status":"ok" if state.get("exit_code") in (None,0) else "error","text":state.pop("output",""),"exit_code":state.get("exit_code"),"meta":state}
    def do_process_write(self,a):
        state=self.processes.write(a["process_id"],a["text"]); return {"text":state.pop("output",""),"exit_code":state.get("exit_code"),"meta":state}
    def do_process_cancel(self,a):
        state=self.processes.cancel(a["process_id"]); return {"status":"cancelled","text":state.pop("output",""),"exit_code":state.get("exit_code"),"meta":state}

class SessionEngine:
    VERSION="h2-local-2"; SCHEMA_VERSION=2
    def __init__(self,workspace,session_id=None,*,budget=None,capability_grant=None,constrained=True):
        self.workspace=Path(workspace).resolve()
        if not self.workspace.is_dir(): raise ValueError("workspace must exist")
        self.session_id=session_id or uuid.uuid4().hex
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}",self.session_id): raise ValueError("invalid session id")
        self.root=self.workspace/".smara"/"sessions"; self.root.mkdir(parents=True,exist_ok=True); self.db_path=self.root/f"{self.session_id}.sqlite3"
        self.artifact_store=ArtifactStore(self.root/self.session_id/"artifacts"); self.artifacts=self.artifact_store.root; self.lock_path=self.root/self.session_id/"writer.lock"
        self.db=sqlite3.connect(self.db_path,timeout=1,isolation_level=None,check_same_thread=False); self._db=self.db
        self.db.execute("PRAGMA journal_mode=WAL"); self.db.execute("PRAGMA synchronous=FULL"); self.migrate()
        self.budget=budget or Budget(); self.broker=ToolBroker(self.workspace,capability_grant or TOOL_SCHEMAS.keys(),constrained=constrained,process_root=self.root/self.session_id/"processes")
        self._runtime_cancellers=[]
    def register_canceller(self,callback):
        if callback not in self._runtime_cancellers:self._runtime_cancellers.append(callback)
    def migrate(self):
        version=self.db.execute("PRAGMA user_version").fetchone()[0]
        if version>self.SCHEMA_VERSION: raise RuntimeError("journal is newer than runtime")
        self.db.executescript("BEGIN IMMEDIATE; CREATE TABLE IF NOT EXISTS events(sequence INTEGER PRIMARY KEY AUTOINCREMENT,type TEXT NOT NULL,payload TEXT NOT NULL,created_at TEXT NOT NULL); CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY,value TEXT NOT NULL); CREATE TABLE IF NOT EXISTS calls(call_id TEXT PRIMARY KEY,position INTEGER UNIQUE NOT NULL,name TEXT NOT NULL,arguments TEXT NOT NULL,workspace_id TEXT NOT NULL,mutating INTEGER NOT NULL,state TEXT NOT NULL,result TEXT,before_revision TEXT,after_revision TEXT); CREATE TABLE IF NOT EXISTS evidence(id TEXT PRIMARY KEY,call_id TEXT,kind TEXT,scope TEXT,subject_revision TEXT,passed INTEGER,source_artifact_id TEXT,created_at TEXT); PRAGMA user_version=2; COMMIT;")
    def event(self,kind,payload): return self.db.execute("INSERT INTO events(type,payload,created_at) VALUES(?,?,?)",(kind,_json(payload),_now())).lastrowid
    def set(self,key,value): self.db.execute("INSERT INTO state VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(key,_json(value)))
    def get(self,key,default=None):
        row=self.db.execute("SELECT value FROM state WHERE key=?",(key,)).fetchone(); return json.loads(row[0]) if row else default
    def _elapsed_wall(self,now: float|None=None) -> float:
        current=time.time() if now is None else now; started=float(self.get("started_wall",current)); paused=float(self.get("paused_seconds",0.0)); paused_at=self.get("paused_at")
        end=float(paused_at) if paused_at is not None else current
        return max(0.0,end-started-paused)
    def pause(self) -> None:
        with _SessionLock(self.lock_path):
            if self.get("paused_at") is None:self.set("paused_at",time.time());self.event("paused",{"elapsed_wall":self._elapsed_wall()})
    def resume_clock(self) -> None:
        with _SessionLock(self.lock_path):
            paused_at=self.get("paused_at")
            if paused_at is not None:
                self.set("paused_seconds",float(self.get("paused_seconds",0.0))+max(0.0,time.time()-float(paused_at)));self.set("paused_at",None);self.event("resumed",{"elapsed_wall":self._elapsed_wall()})
    def inspect(self):
        state={k:json.loads(v) for k,v in self.db.execute("SELECT key,value FROM state")}; events=[{"version":1,"session_id":self.session_id,"sequence":r[0],"type":r[1],"timestamp":r[3],"payload":json.loads(r[2])} for r in self.db.execute("SELECT sequence,type,payload,created_at FROM events ORDER BY sequence")]
        calls=[{"call_id":r[0],"position":r[1],"name":r[2],"arguments":json.loads(r[3]),"workspace_id":r[4],"mutating":bool(r[5]),"state":r[6],"result":json.loads(r[7]) if r[7] else None,"before_revision":r[8],"after_revision":r[9]} for r in self.db.execute("SELECT call_id,position,name,arguments,workspace_id,mutating,state,result,before_revision,after_revision FROM calls ORDER BY position")]
        evidence=[{"id":r[0],"call_id":r[1],"kind":r[2],"scope":r[3],"subject_revision":r[4],"passed":bool(r[5]),"source_artifact_id":r[6],"created_at":r[7]} for r in self.db.execute("SELECT id,call_id,kind,scope,subject_revision,passed,source_artifact_id,created_at FROM evidence ORDER BY created_at")]
        return {"engine_version":self.VERSION,"session_id":self.session_id,"workspace":str(self.workspace),"state":state,"events":events,"calls":calls,"evidence":evidence}
    def cancel(self):
        # Cancellation is the one permitted concurrent writer: requiring the
        # run lock here would make it impossible to cancel an active session.
        self.set("cancelled",True); self.event("cancel_requested",{})
        for record in self.inspect()["calls"]:
            result=record.get("result") or {}; process_id=(result.get("meta") or {}).get("process_id")
            if process_id:
                with contextlib.suppress(Exception): self.broker.processes.cancel(process_id)
        for callback in tuple(self._runtime_cancellers):
            with contextlib.suppress(Exception):callback()
    def close(self): self.db.close()
    def store_plan(self,request,calls,resume):
        if resume:
            if self.get("request") is None: raise ValueError("uninitialized session")
            return
        if self.inspect()["calls"]: raise ValueError("session exists; resume it")
        self.set("request",request); self.set("started_wall",time.time()); self.set("paused_seconds",0.0); self.set("paused_at",None); self.set("usage",{"tool_calls":0,"model_calls":0,"billed_tokens":0,"dollars":0}); self.set("budget",asdict(self.budget)); self.event("started",{"request":request,"engine_version":self.VERSION})
        for pos,raw in enumerate(calls):
            cid=str(raw.get("call_id") or f"{self.session_id}-{pos}"); name=str(raw.get("name") or ""); args=raw.get("arguments",raw.get("args",{})); args=args if isinstance(args,dict) else {"__invalid__":args}; wid=str(raw.get("workspace_id") or self.workspace)
            self.db.execute("INSERT INTO calls VALUES(?,?,?,?,?,?,?,NULL,NULL,NULL)",(cid,pos,name,_json(args),wid,int(name in MUTATING_TOOLS or name=="agent_turn"),"pending"))
    def run(self,request,calls,executor=None,*,resume=False):
        with _SessionLock(self.lock_path): self.store_plan(request,calls,resume); return self.continue_run(executor)
    def resume(self,executor=None):
        with _SessionLock(self.lock_path): return self.continue_run(executor)
    def begin_incremental(self, request: str) -> None:
        """Initialize a step-wise agent session without an opaque agent_turn."""
        with _SessionLock(self.lock_path):
            if self.get("request") is None:
                self.set("request",request); self.set("started_wall",time.time()); self.set("paused_seconds",0.0); self.set("paused_at",None); self.set("usage",{"tool_calls":0,"model_calls":0,"billed_tokens":0,"dollars":0}); self.set("budget",asdict(self.budget)); self.event("started",{"request":request,"engine_version":self.VERSION,"mode":"incremental"})
            elif self.get("request") != request:
                raise ValueError("session objective does not match persisted request")
    def begin_request(self,request:RunRequest) -> None:
        if request.session_id!=self.session_id:raise ValueError("request session_id does not match engine")
        requested_workspace=Path(request.workspace_id).resolve()
        if requested_workspace!=self.workspace:raise ValueError("request workspace_id does not match engine")
        self.budget=request.budget;self.broker=ToolBroker(self.workspace,request.capability_grant or TOOL_SCHEMAS.keys(),constrained=self.broker.constrained,process_root=self.root/self.session_id/"processes")
        self.begin_incremental(request.user_message);self.set("attachments",list(request.attachments));self.set("output_contract",dict(request.output_contract));self.set("model_profile",request.model_profile)
    @staticmethod
    def _output_contract_errors(answer:str,contract:Mapping[str,Any]) -> list[str]:
        errors=[]
        if contract.get("required") and not answer.strip():errors.append("answer is required")
        if "min_length" in contract and len(answer)<int(contract["min_length"]):errors.append("answer is shorter than min_length")
        if "max_length" in contract and len(answer)>int(contract["max_length"]):errors.append("answer exceeds max_length")
        if contract.get("required_pattern") and not re.search(str(contract["required_pattern"]),answer):errors.append("answer does not match required_pattern")
        if contract.get("format")=="json":
            try:value=json.loads(answer)
            except (TypeError,ValueError):errors.append("answer is not valid JSON");value=None
            if isinstance(value,dict):
                missing=[str(key) for key in contract.get("required_fields",()) if key not in value]
                if missing:errors.append("answer JSON is missing fields: "+", ".join(missing))
        return errors
    def reserve_model_call(self, estimated_tokens: int, estimated_dollars: float=0.0) -> str:
        """Atomically reserve aggregate model budget before provider dispatch."""
        with _SessionLock(self.lock_path):
            budget=Budget(**self.get("budget",asdict(self.budget))); usage=self.get("usage",{})
            if self.get("cancelled",False): raise BudgetExceeded("cancelled")
            if self.get("paused_at") is not None: raise BudgetExceeded("paused")
            if self._elapsed_wall() >= budget.wall_seconds: raise BudgetExceeded("wall_seconds")
            if int(usage.get("model_calls",0))+1 > budget.model_calls: raise BudgetExceeded("model_calls")
            conservative=max(1,int(estimated_tokens))
            if int(usage.get("billed_tokens",0))+conservative > budget.billed_tokens: raise BudgetExceeded("billed_tokens")
            if float(usage.get("dollars",0))+estimated_dollars > budget.dollars: raise BudgetExceeded("dollars")
            reservation=uuid.uuid4().hex; usage["model_calls"]=int(usage.get("model_calls",0))+1; usage["billed_tokens"]=int(usage.get("billed_tokens",0))+conservative; usage["dollars"]=float(usage.get("dollars",0))+estimated_dollars; self.set("usage",usage); reservations=self.get("model_reservations",{}); reservations[reservation]={"estimated_tokens":conservative,"estimated_dollars":estimated_dollars,"attempts":1}; self.set("model_reservations",reservations)
            self.event("provider_request",{"reservation_id":reservation,"estimated_tokens":conservative,"accounting_quality":"conservative"}); return reservation
    def reserve_child_budget(self, allocation: Budget, *, depth: int, max_depth: int=1, max_fanout: int=4) -> str:
        with _SessionLock(self.lock_path):
            if self.get("cancelled",False):raise BudgetExceeded("cancelled")
            if self.get("paused_at") is not None:raise BudgetExceeded("paused")
            if depth>max_depth: raise BudgetExceeded("child_depth")
            root=Budget(**self.get("budget",asdict(self.budget))); used=self.get("usage",{}); reservations=self.get("child_reservations",{})
            active=[item for item in reservations.values() if item.get("status")=="reserved"]
            if len(active)>=max_fanout: raise BudgetExceeded("child_fanout")
            reserved_tools=sum(item["budget"]["tool_calls"] for item in active); reserved_models=sum(item["budget"]["model_calls"] for item in active); reserved_tokens=sum(item["budget"]["billed_tokens"] for item in active); reserved_dollars=sum(item["budget"]["dollars"] for item in active)
            if int(used.get("tool_calls",0))+reserved_tools+allocation.tool_calls>root.tool_calls: raise BudgetExceeded("child_tool_calls")
            if int(used.get("model_calls",0))+reserved_models+allocation.model_calls>root.model_calls: raise BudgetExceeded("child_model_calls")
            if int(used.get("billed_tokens",0))+reserved_tokens+allocation.billed_tokens>root.billed_tokens: raise BudgetExceeded("child_tokens")
            if float(used.get("dollars",0))+reserved_dollars+allocation.dollars>root.dollars: raise BudgetExceeded("child_dollars")
            ident=f"child_{uuid.uuid4().hex}"; reservations[ident]={"budget":asdict(allocation),"depth":depth,"status":"reserved"}; self.set("child_reservations",reservations); self.event("child_budget_reserved",{"reservation_id":ident,"budget":asdict(allocation),"depth":depth}); return ident
    def reconcile_child_budget(self,reservation_id: str,usage: Mapping[str,Any]) -> None:
        with _SessionLock(self.lock_path):
            reservations=self.get("child_reservations",{}); item=reservations.get(reservation_id)
            if not item or item.get("status")!="reserved": raise ValueError("invalid child reservation")
            limits=item["budget"]
            for key in ("tool_calls","model_calls","billed_tokens","dollars"):
                if float(usage.get(key,0))>float(limits[key]): raise BudgetExceeded(f"child_{key}")
            root_usage=self.get("usage",{})
            for key in ("tool_calls","model_calls","billed_tokens","dollars"): root_usage[key]=root_usage.get(key,0)+usage.get(key,0)
            item["status"]="reconciled"; item["usage"]=dict(usage); self.set("usage",root_usage); self.set("child_reservations",reservations); self.event("child_budget_reconciled",{"reservation_id":reservation_id,"usage":dict(usage)})
    def reserve_model_retry(self,reservation_id: str) -> None:
        with _SessionLock(self.lock_path):
            reservations=self.get("model_reservations",{}); reservation=reservations.get(reservation_id)
            if not reservation: raise BudgetExceeded("missing_retry_reservation")
            budget=Budget(**self.get("budget",asdict(self.budget))); usage=self.get("usage",{}); estimate=int(reservation["estimated_tokens"]);estimated_dollars=float(reservation.get("estimated_dollars",0.0))
            if self.get("cancelled",False):raise BudgetExceeded("cancelled")
            if self.get("paused_at") is not None:raise BudgetExceeded("paused")
            if self._elapsed_wall()>=budget.wall_seconds:raise BudgetExceeded("wall_seconds")
            if int(usage.get("model_calls",0))+1>budget.model_calls: raise BudgetExceeded("model_calls")
            if int(usage.get("billed_tokens",0))+estimate>budget.billed_tokens: raise BudgetExceeded("billed_tokens")
            if float(usage.get("dollars",0))+estimated_dollars>budget.dollars:raise BudgetExceeded("dollars")
            usage["model_calls"]+=1; usage["billed_tokens"]+=estimate;usage["dollars"]=float(usage.get("dollars",0))+estimated_dollars; reservation["attempts"]+=1; self.set("usage",usage); self.set("model_reservations",reservations); self.event("provider_retry_reserved",{"reservation_id":reservation_id,"attempt":reservation["attempts"],"estimated_dollars":estimated_dollars})
    def reconcile_model_call(self,reservation_id: str,*,actual_tokens: int|None,provider_request_id: str|None,status: str,retries: int=0) -> None:
        # Unknown provider usage remains charged at the conservative reservation.
        with _SessionLock(self.lock_path):
            reservations=self.get("model_reservations",{}); reservation=reservations.get(reservation_id)
            if reservation and actual_tokens is not None:
                usage=self.get("usage",{}); usage["billed_tokens"]=max(0,int(usage.get("billed_tokens",0))-int(reservation["estimated_tokens"])+int(actual_tokens)); self.set("usage",usage)
            self.event("provider_response",{"reservation_id":reservation_id,"provider_request_id":provider_request_id,"status":status,"actual_tokens":actual_tokens,"usage_known":actual_tokens is not None,"retries":retries})
    def checkpoint(self, messages: list[dict[str,Any]], state: Mapping[str,Any]) -> None:
        with _SessionLock(self.lock_path):
            from smara.continuation import ContinuationState
            self.set("agent_messages",messages); self.set("agent_state",dict(state))
            evidence=list(self.db.execute("SELECT id,passed FROM evidence ORDER BY created_at")); calls=self.inspect()["calls"]
            usage=self.get("usage",{});budget=Budget(**self.get("budget",asdict(self.budget)));remaining={"wall_seconds":max(0,budget.wall_seconds-self._elapsed_wall()),"tool_calls":max(0,budget.tool_calls-int(usage.get("tool_calls",0))),"model_calls":max(0,budget.model_calls-int(usage.get("model_calls",0))),"billed_tokens":max(0,budget.billed_tokens-int(usage.get("billed_tokens",0))),"dollars":max(0,budget.dollars-float(usage.get("dollars",0)))}
            process_handles={}
            for item in calls:
                result=item.get("result") or {};meta=result.get("meta") or {};process_id=meta.get("process_id")
                if process_id:
                    if item["name"]=="process_cancel" or result.get("exit_code") is not None:process_handles.pop(process_id,None)
                    else:process_handles[process_id]={"kind":"process","process_id":process_id,"call_id":item["call_id"]}
            supplied_handles=[dict(item) for item in state.get("active_handles",()) if isinstance(item,Mapping)]
            browser_handle=self.get("browser_handle")
            if isinstance(browser_handle,Mapping):supplied_handles.append(dict(browser_handle))
            supplied_research=state.get("research_artifact_ids",())
            research_ids=tuple(dict.fromkeys([*[str(item) for item in supplied_research],*([str(self.get("research_state_artifact_id"))] if self.get("research_state_artifact_id") else [])]))
            continuation=ContinuationState(objective=str(self.get("request", "")),constraints=tuple(state.get("constraints",())),acceptance_criteria=tuple(state.get("acceptance_criteria",())),tasks=tuple(state.get("tasks",())),decisions=tuple(state.get("decisions",())),unresolved_questions=tuple(state.get("unresolved_questions",())),changed_paths=tuple(sorted({path for call in calls for path in ((call.get("result") or {}).get("changed_paths") or [])})),workspace_revision=workspace_revision(self.workspace),failed_evidence_ids=tuple(row[0] for row in evidence if not row[1]),passing_evidence_ids=tuple(row[0] for row in evidence if row[1]),research_artifact_ids=research_ids,pending_call_ids=tuple(call["call_id"] for call in calls if call["state"]=="pending"),uncertain_call_ids=tuple(call["call_id"] for call in calls if call["state"]=="admitted" and call["mutating"]),active_handles=tuple([*process_handles.values(),*supplied_handles]),usage=usage,remaining_budget=remaining,next_action=str(state.get("next_action") or state.get("phase") or "model"),parent_checkpoint_id=self.get("continuation_artifact_id"))
            artifact_id,_=self.artifact_store.put_json(continuation.to_dict()); self.set("continuation_artifact_id",artifact_id); self.event("checkpoint",{"message_count":len(messages),"state_sha256":_sha(_json(state).encode()),"continuation_artifact_id":artifact_id,"parent_checkpoint_id":continuation.parent_checkpoint_id})
    def resolve_artifact(self,artifact_id: str) -> bytes:
        matches=list(self.artifacts.glob(f"{artifact_id}.*"))
        if not matches: raise FileNotFoundError(artifact_id)
        values=[item.read_bytes() for item in matches]
        if any(_sha(data)!=artifact_id for data in values) or any(data!=values[0] for data in values[1:]): raise ValueError("artifact hash mismatch")
        data=values[0]
        return data
    def execute_incremental(self,call: ToolCall,executor: Callable[[dict[str,Any]],ToolResult]) -> ToolResult:
        """Journal one real model-emitted call before executing it."""
        with _SessionLock(self.lock_path):
            from smara.progress import ProgressRecord, classify, fingerprint, result_hash
            if self.get("cancelled",False):raise BudgetExceeded("cancelled")
            if self.get("paused_at") is not None:raise BudgetExceeded("paused")
            budget=Budget(**self.get("budget",asdict(self.budget)))
            if self._elapsed_wall()>=budget.wall_seconds:raise BudgetExceeded("wall_seconds")
            current_revision=workspace_revision(self.workspace); fp=fingerprint(call.name,call.arguments,current_revision)
            cache=self.get("receipt_cache",{})
            if call.name in REUSABLE_READ_TOOLS and fp in cache:
                cached={k:v for k,v in cache[fp].items() if k!="artifact_id"}; cached["call_id"]=call.call_id
                existing=self.db.execute("SELECT state FROM calls WHERE call_id=?",(call.call_id,)).fetchone()
                if not existing:
                    pos=self.db.execute("SELECT COALESCE(MAX(position),-1)+1 FROM calls").fetchone()[0]
                    receipt={**cached,"artifact_id":cache[fp].get("artifact_id"),"reused":True}
                    self.db.execute("INSERT INTO calls VALUES(?,?,?,?,?,?,?,NULL,?,?)",(call.call_id,pos,call.name,_json(call.arguments),call.workspace_id,0,"completed",current_revision,_json(receipt)))
                reuse_counts=self.get("receipt_reuse_counts",{}); count=int(reuse_counts.get(fp,0))+1; reuse_counts[fp]=count; self.set("receipt_reuse_counts",reuse_counts)
                action="strategy_change" if count==2 else "stop" if count>=3 else "cached_result"
                self.event("tool_result_reused",{"call_id":call.call_id,"source_call_id":cache[fp].get("call_id"),"fingerprint":fp,"reuse_count":count,"action":action})
                if action in {"strategy_change","stop"}: self.event("stall",{"call_id":call.call_id,"action":action,"reason":"repeated_cached_read"})
                result=ToolResult(**cached)
                if action=="strategy_change": return replace(result,text=result.text+"\n[SMARA: cached observation repeated; choose a materially different strategy.]")
                if action=="stop": return replace(result,status="error",text="Repeated unchanged read stopped after bounded recovery.",error_kind="stall_detected")
                return result
            existing=self.db.execute("SELECT state,result,mutating FROM calls WHERE call_id=?",(call.call_id,)).fetchone()
            if existing:
                if existing[0]=="completed": return ToolResult(**{k:v for k,v in json.loads(existing[1]).items() if k!="artifact_id"})
                if existing[0]=="admitted" and existing[2]: raise RuntimeError(f"uncertain mutation: {call.call_id}")
            else:
                pos=self.db.execute("SELECT COALESCE(MAX(position),-1)+1 FROM calls").fetchone()[0]; self.db.execute("INSERT INTO calls VALUES(?,?,?,?,?,?,?,NULL,NULL,NULL)",(call.call_id,pos,call.name,_json(call.arguments),call.workspace_id,int(call.name in MUTATING_TOOLS or call.name in {"patch","file_write","terminal","python_execute"}),"pending"))
            usage=self.get("usage",{})
            if int(usage.get("tool_calls",0))+1>budget.tool_calls: raise BudgetExceeded("tool_calls")
            usage["tool_calls"]=int(usage.get("tool_calls",0))+1; self.set("usage",usage); before=workspace_revision(self.workspace); self.db.execute("UPDATE calls SET state='admitted',before_revision=? WHERE call_id=?",(before,call.call_id)); self.event("tool_admitted",{"call_id":call.call_id,"name":call.name,"arguments_sha256":_sha(_json(call.arguments).encode())})
            result=executor({"call_id":call.call_id,"name":call.name,"arguments":dict(call.arguments),**dict(call.arguments)})
            if not isinstance(result,ToolResult) or result.call_id!=call.call_id: result=ToolResult(call.call_id,"error","invalid executor result",error_kind="invalid_result")
            receipt=asdict(result); aid,_=self.artifact_store.put_json(receipt); receipt["artifact_id"]=aid; after=result.after_revision or workspace_revision(self.workspace); self.db.execute("UPDATE calls SET state='completed',result=?,after_revision=? WHERE call_id=?",(_json(receipt),after,call.call_id)); self.event("tool_result",{**receipt,"source_artifact_id":aid})
            scope=str(result.meta.get("evidence_scope","none"))
            if scope in VERIFY_SCOPES:
                ev=Evidence(uuid.uuid4().hex,call.call_id,"test",scope,after,result.ok,aid,_now()); self.db.execute("INSERT INTO evidence VALUES(?,?,?,?,?,?,?,?)",(ev.id,ev.call_id,ev.kind,ev.scope,ev.subject_revision,int(ev.passed),ev.source_artifact_id,ev.created_at)); self.event("evidence",asdict(ev))
            if call.name in REUSABLE_READ_TOOLS and result.ok: cache[fp]=receipt; self.set("receipt_cache",cache)
            window=self.get("progress_window",[]); record=ProgressRecord(fp,call.call_id,call.name,after,result_hash(result.text),result.ok,before!=after or scope in VERIFY_SCOPES,"revision_changed" if before!=after else "evidence" if scope in VERIFY_SCOPES else "observation")
            action,reason=classify(window,record); window=([*window,asdict(record)])[-12:]; self.set("progress_window",window); self.event("progress",{**asdict(record),"action":action,"stall_reason":reason})
            if action:self.event("stall",{"call_id":call.call_id,"action":action,"reason":reason})
            if action=="recover": return replace(result,text=result.text+"\n[SMARA: progress stalled; make one bounded, materially different recovery attempt.]")
            if action=="stop": return replace(result,status="error",text="Repeated identical failure stopped.",error_kind="stall_detected")
            return result
    def finish_incremental(self,status: str,answer: str,unresolved: Iterable[str]=()) -> dict[str,Any]:
        with _SessionLock(self.lock_path):
            revision=workspace_revision(self.workspace); mutated=self.db.execute("SELECT COUNT(*) FROM calls WHERE mutating=1 AND state='completed' AND COALESCE(before_revision,'')<>COALESCE(after_revision,'')").fetchone()[0]>0; verified=self.db.execute("SELECT COUNT(*) FROM evidence WHERE passed=1 AND subject_revision=? AND scope IN ('focused','full')",(revision,)).fetchone()[0]>0
            if status=="completed" and mutated and not verified: status="needs_input"; unresolved=tuple(unresolved)+("workspace changes are unverified at current revision",)
            contract_errors=self._output_contract_errors(answer,self.get("output_contract",{}))
            if status=="completed" and contract_errors:status="needs_input";unresolved=tuple(unresolved)+tuple(contract_errors)
            research_validation=self.get("research_validation",{})
            if status=="completed" and self.get("research_required",False):
                research_artifact=self.get("research_state_artifact_id");validated_artifact=self.get("research_validated_state_artifact_id");research_valid=bool(research_validation.get("passed",False) and research_artifact and research_artifact==validated_artifact and not self.get("research_state_invalid"))
                if research_valid:
                    try:self.resolve_artifact(research_artifact)
                    except (FileNotFoundError,ValueError):research_valid=False
                if not research_valid:status="needs_input";unresolved=tuple(unresolved)+("required research claims lack current artifact-backed validation",)
            receipts=[r[0] for r in self.db.execute("SELECT result FROM calls WHERE result IS NOT NULL ORDER BY position")]; verification=tuple(json.loads(item) for item in receipts); result=RunResult(status,answer,(),verification,self.get("usage",{}),tuple(unresolved),self.session_id,self.session_id,self.VERSION).to_dict(); self.set("result",result); self.event("finished",{"status":status,"unresolved_items":list(unresolved)}); return result
    def continue_run(self,executor):
        if self.get("cancelled",False): return self.finish("cancelled",(),("cancelled by user",))
        budget=Budget(**self.get("budget",asdict(self.budget))); usage=self.get("usage",{}); receipts=[]
        for rec in self.inspect()["calls"]:
            if rec["state"]=="completed":
                if rec["result"]: receipts.append(rec["result"])
                continue
            if rec["state"]=="admitted":
                if rec["mutating"]: return self.finish("needs_input",receipts,(f"uncertain mutation: {rec['call_id']}",))
                self.db.execute("UPDATE calls SET state='pending' WHERE call_id=?",(rec["call_id"],))
            if self.get("cancelled",False): return self.finish("cancelled",receipts,("cancelled by user",))
            if self.get("paused_at") is not None:return self.finish("interrupted",receipts,("session is paused",))
            if self._elapsed_wall()>=budget.wall_seconds or int(usage.get("tool_calls",0))>=budget.tool_calls: self.event("budget_exhausted",{}); return self.finish("budget_exhausted",receipts,("budget exhausted",))
            usage["tool_calls"]=int(usage.get("tool_calls",0))+1; self.set("usage",usage); before=workspace_revision(self.workspace)
            self.db.execute("UPDATE calls SET state='admitted',before_revision=? WHERE call_id=?",(before,rec["call_id"])); self.event("tool_admitted",{"call_id":rec["call_id"],"name":rec["name"],"arguments_sha256":_sha(_json(rec["arguments"]).encode())})
            call=ToolCall(rec["call_id"],rec["name"],rec["arguments"],rec["workspace_id"])
            try:
                if executor is None: result=self.broker.dispatch(call)
                else:
                    compat={"call_id":call.call_id,"name":call.name,"arguments":dict(call.arguments),**dict(call.arguments)}; result=executor(compat)
                    if not isinstance(result,ToolResult) or result.call_id!=call.call_id: raise TypeError("executor returned invalid ToolResult")
            except KeyboardInterrupt: self.event("interrupted",{"call_id":call.call_id}); raise
            except Exception as exc: result=ToolResult(call.call_id,"error",f"{type(exc).__name__}: {exc}",error_kind=type(exc).__name__,before_revision=before,after_revision=workspace_revision(self.workspace))
            receipt=asdict(result); aid,_=self.artifact_store.put_json(receipt); receipt["artifact_id"]=aid; after=result.after_revision or workspace_revision(self.workspace)
            self.db.execute("UPDATE calls SET state='completed',result=?,after_revision=? WHERE call_id=?",(_json(receipt),after,call.call_id)); self.event("tool_result",{**receipt,"source_artifact_id":aid}); receipts.append(receipt)
            scope=str(result.meta.get("evidence_scope","none"))
            if scope in VERIFY_SCOPES:
                ev=Evidence(uuid.uuid4().hex,call.call_id,"test",scope,after,result.ok,aid,_now()); self.db.execute("INSERT INTO evidence VALUES(?,?,?,?,?,?,?,?)",(ev.id,ev.call_id,ev.kind,ev.scope,ev.subject_revision,int(ev.passed),ev.source_artifact_id,ev.created_at)); self.event("evidence",asdict(ev))
            if not result.ok: return self.finish("denied" if result.status=="denied" else "cancelled" if result.status=="cancelled" else "tool_error",receipts,(result.error_kind or result.status,))
        revision=workspace_revision(self.workspace); mutated=self.db.execute("SELECT COUNT(*) FROM calls WHERE mutating=1 AND state='completed' AND COALESCE(before_revision,'')<>COALESCE(after_revision,'')").fetchone()[0]>0; passing=self.db.execute("SELECT COUNT(*) FROM evidence WHERE passed=1 AND subject_revision=? AND scope IN ('focused','full')",(revision,)).fetchone()[0]>0
        if mutated and not passing: return self.finish("needs_input",receipts,("workspace changes are unverified at current revision",))
        return self.finish("completed",receipts,())
    def finish(self,status,receipts,unresolved):
        if status not in RUN_STATUSES: raise ValueError(status)
        answer=""
        for record in self.inspect()["calls"]:
            if record["name"]=="agent_turn" and record.get("result"): answer=str(record["result"].get("text") or "")
        result=RunResult(status,answer,(),tuple(receipts),self.get("usage",{}),tuple(unresolved),self.session_id,self.session_id,self.VERSION).to_dict(); prior=self.get("result"); self.set("result",result)
        if prior!=result:self.event("finished",{"status":status,"unresolved_items":list(unresolved)})
        return result

__all__=["ArtifactStore","Budget","BUDGET_PROFILES","Evidence","ProcessSupervisor","RunEvent","RunRequest","RunResult","SchemaError","SessionBusy","SessionEngine","ToolBroker","ToolCall","ToolResult","workspace_revision"]
