"""Disposable-VM-only desktop control contract.

No native host implementation is provided intentionally. A transport must
attest a reset snapshot before actions are admitted.
"""
from __future__ import annotations
import hashlib,os,re,subprocess,time,uuid
from dataclasses import dataclass
from typing import Any,Protocol

class DesktopTransport(Protocol):
    def reset_identity(self)->str:...
    def screenshot(self)->tuple[bytes,dict[str,Any]]:...
    def action(self,payload:dict[str,Any])->dict[str,Any]:...
class StaleDesktopObservation(RuntimeError):pass
class UnsafeDesktopBackend(RuntimeError):pass
class DesktopDeadlineExceeded(TimeoutError):pass


class DockerDesktopTransport:
    """Bounded transport for a disposable Docker X11 guest.

    The guest is intentionally not the Windows desktop.  It is an isolated
    Linux GUI (Xvfb + Openbox) whose reset label is attested before every
    action.  The transport never invokes a host shell and only admits a small
    xdotool action allow-list.
    """

    _NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$")
    _LAUNCH_ALLOWLIST = {"xterm", "xmessage", "xclock"}

    def __init__(self, container_name: str, *, docker_bin: str = "docker"):
        if not self._NAME.fullmatch(container_name):
            raise ValueError("invalid Docker desktop container name")
        self.container_name = container_name
        self.docker_bin = docker_bin

    @classmethod
    def start(cls, *, image: str = "smara-desktop-gate:local", container_name: str | None = None, docker_bin: str = "docker") -> "DockerDesktopTransport":
        name = container_name or f"smara-desktop-{uuid.uuid4().hex[:12]}"
        if not cls._NAME.fullmatch(name):
            raise ValueError("invalid Docker desktop container name")
        reset_id = uuid.uuid4().hex
        result = subprocess.run(
            [docker_bin, "run", "-d", "--rm", "--name", name, "--label", f"smara.reset_id={reset_id}", image],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode:
            raise UnsafeDesktopBackend("Docker desktop guest failed to start")
        return cls(name, docker_bin=docker_bin)

    def _run(self, *argv: str, timeout: float = 10, binary: bool = False):
        result = subprocess.run(
            [self.docker_bin, *argv], capture_output=True, timeout=timeout,
            text=not binary,
        )
        if result.returncode:
            detail = (result.stderr or result.stdout or "").strip()[:240]
            raise UnsafeDesktopBackend(f"Docker desktop guest command failed: {detail}")
        return result.stdout

    def reset_identity(self) -> str:
        value = self._run(
            "inspect", "--format", "{{.State.Running}}|{{index .Config.Labels \"smara.reset_id\"}}|{{.Id}}",
            self.container_name,
        ).strip().split("|", 2)
        if len(value) != 3 or value[0].lower() != "true" or not value[1] or not value[2]:
            return ""
        return f"docker:{value[1]}:{value[2][:16]}"

    def screenshot(self) -> tuple[bytes, dict[str, Any]]:
        info = self._run("exec", self.container_name, "xdpyinfo", timeout=10)
        match = re.search(r"dimensions:\s+(\d+)x(\d+) pixels", info)
        if not match:
            raise UnsafeDesktopBackend("Docker desktop guest returned no display geometry")
        width, height = int(match.group(1)), int(match.group(2))
        pixels = self._run("exec", self.container_name, "/usr/local/bin/smara-screenshot", timeout=15, binary=True)
        return pixels, {
            "pixel_width": width, "pixel_height": height,
            "guest_width": width, "guest_height": height,
            "origin_x": 0, "origin_y": 0,
            "window_id": "root", "display_id": os.getenv("SMARA_DOCKER_DISPLAY", ":99"),
            "dpi_scale": 1.0,
        }

    def action(self, payload: dict[str, Any]) -> dict[str, Any]:
        kind = str(payload.get("kind") or "")
        if kind == "click":
            self._run("exec", self.container_name, "xdotool", "mousemove", str(int(payload["x"])), str(int(payload["y"])))
            self._run("exec", self.container_name, "xdotool", "click", "1")
        elif kind == "type":
            self._run("exec", self.container_name, "xdotool", "type", "--delay", "1", "--", str(payload.get("text") or ""))
        elif kind == "hotkey":
            raw_keys = str(payload.get("text") or "").strip()
            if not raw_keys:
                raise UnsafeDesktopBackend("Docker desktop hotkey cannot be empty")
            normalized: list[str] = []
            for raw_key in raw_keys.split("+"):
                key = raw_key.strip().casefold()
                aliases = {"control": "ctrl", "return": "Return", "esc": "Escape", "del": "Delete"}
                key = aliases.get(key, key)
                if not re.fullmatch(r"[a-z0-9_]+", key) and key not in {"Return", "Escape", "Delete"}:
                    raise UnsafeDesktopBackend("Docker desktop hotkey contains an invalid key")
                normalized.append(key)
            keys = "+".join(normalized)
            self._run("exec", self.container_name, "xdotool", "key", keys)
        elif kind == "scroll":
            delta = int(payload.get("delta_y") or payload.get("scroll_y") or 0)
            button = "4" if delta > 0 else "5"
            for _ in range(min(20, max(1, abs(delta) // 100))):
                self._run("exec", self.container_name, "xdotool", "click", button)
        elif kind == "clipboard":
            self._run("exec", self.container_name, "sh", "-lc", "printf %s \"$1\" | xclip -selection clipboard", "smara", str(payload.get("text") or ""))
        elif kind == "launch":
            argv = payload.get("argv") or [str(payload.get("text") or "xterm")]
            if not isinstance(argv, list) or not argv or str(argv[0]) not in self._LAUNCH_ALLOWLIST:
                raise UnsafeDesktopBackend("Docker desktop launch command is not allow-listed")
            self._run("exec", "-d", self.container_name, *[str(item) for item in argv])
        else:
            raise UnsafeDesktopBackend(f"Docker desktop action is not supported: {kind}")
        return {"accepted": True, "state": "changed", "reset_id": self.reset_identity()}

    def cancel(self) -> None:
        subprocess.run([self.docker_bin, "stop", "-t", "2", self.container_name], capture_output=True, timeout=10)

    def close(self) -> None:
        subprocess.run([self.docker_bin, "rm", "-f", self.container_name], capture_output=True, timeout=10)

@dataclass(frozen=True)
class DisplayTransform:
    pixel_width:int; pixel_height:int; guest_width:int; guest_height:int; origin_x:int=0; origin_y:int=0
    def to_guest(self,x:float,y:float):
        if not 0<=x<self.pixel_width or not 0<=y<self.pixel_height:raise ValueError("coordinate outside observation")
        return self.origin_x+round(x*self.guest_width/self.pixel_width),self.origin_y+round(y*self.guest_height/self.pixel_height)

class IsolatedDesktop:
    ACTIONS={"launch","focus","type","hotkey","click","drag","scroll","save","open","window_move","resize","dialog","clipboard"}
    def __init__(self,transport:DesktopTransport,*,deadline_seconds:float=60,action_budget:int=40):
        self.transport=transport; self.reset_id=transport.reset_identity()
        if not self.reset_id:raise UnsafeDesktopBackend("desktop transport did not attest a disposable reset snapshot")
        if deadline_seconds<=0 or action_budget<1:raise ValueError("invalid desktop limits")
        self.generation=0; self.observations={}; self.closed=False;self.cancelled=False;self.deadline=time.monotonic()+deadline_seconds;self.action_budget=action_budget;self.actions=0
    def _admit(self):
        if self.closed or self.cancelled:raise RuntimeError("desktop session is closed")
        if time.monotonic()>=self.deadline:raise DesktopDeadlineExceeded("desktop deadline exceeded")
        if self.actions>=self.action_budget:raise DesktopDeadlineExceeded("desktop action budget exhausted")
    def observe(self):
        self._admit()
        pixels,meta=self.transport.screenshot(); required={"pixel_width","pixel_height","guest_width","guest_height","origin_x","origin_y","window_id","display_id","dpi_scale"}
        if not required<=set(meta):raise ValueError("incomplete desktop observation metadata")
        if min(int(meta[key]) for key in ("pixel_width","pixel_height","guest_width","guest_height"))<=0 or float(meta["dpi_scale"])<=0:raise ValueError("invalid desktop geometry")
        self.generation+=1; ident=f"desktop_obs_{uuid.uuid4().hex[:16]}"; observation={**meta,"observation_id":ident,"generation":self.generation,"reset_id":self.reset_id,"timestamp":time.time(),"screenshot_sha256":hashlib.sha256(pixels).hexdigest()}; self.observations[ident]=observation; return observation
    def act(self,observation_id,kind,*,x=None,y=None,end_x=None,end_y=None,text=None,**parameters):
        self._admit()
        if kind not in self.ACTIONS:raise ValueError("unsupported desktop action")
        observation=self.observations.get(observation_id)
        if not observation or observation["generation"]!=self.generation:raise StaleDesktopObservation("desktop observation is stale")
        if self.transport.reset_identity()!=self.reset_id:raise StaleDesktopObservation("VM reset identity changed")
        payload={"kind":kind,"window_id":observation["window_id"],"display_id":observation["display_id"],"observation_id":observation_id}
        if x is not None or y is not None:
            if x is None or y is None:raise ValueError("both coordinates are required")
            transform=DisplayTransform(**{key:observation[key] for key in ("pixel_width","pixel_height","guest_width","guest_height","origin_x","origin_y")}); payload["x"],payload["y"]=transform.to_guest(float(x),float(y))
            if end_x is not None or end_y is not None:
                if end_x is None or end_y is None:raise ValueError("both drag end coordinates are required")
                payload["end_x"],payload["end_y"]=transform.to_guest(float(end_x),float(end_y))
        if text is not None:payload["text"]=str(text)
        payload.update(parameters);payload["deadline_monotonic"]=self.deadline;result=self.transport.action(payload); self.generation+=1;self.actions+=1
        if not result.get("accepted"):raise RuntimeError("guest rejected desktop action")
        return result
    def cancel(self):
        self.cancelled=True
        cancel=getattr(self.transport,"cancel",None)
        if callable(cancel):cancel()
    def close(self):self.closed=True
