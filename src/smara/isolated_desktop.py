"""Disposable-VM-only desktop control contract.

No native host implementation is provided intentionally. A transport must
attest a reset snapshot before actions are admitted.
"""
from __future__ import annotations
import hashlib,time,uuid
from dataclasses import dataclass
from typing import Any,Protocol

class DesktopTransport(Protocol):
    def reset_identity(self)->str:...
    def screenshot(self)->tuple[bytes,dict[str,Any]]:...
    def action(self,payload:dict[str,Any])->dict[str,Any]:...
class StaleDesktopObservation(RuntimeError):pass
class UnsafeDesktopBackend(RuntimeError):pass

@dataclass(frozen=True)
class DisplayTransform:
    pixel_width:int; pixel_height:int; guest_width:int; guest_height:int; origin_x:int=0; origin_y:int=0
    def to_guest(self,x:float,y:float):
        if not 0<=x<self.pixel_width or not 0<=y<self.pixel_height:raise ValueError("coordinate outside observation")
        return self.origin_x+round(x*self.guest_width/self.pixel_width),self.origin_y+round(y*self.guest_height/self.pixel_height)

class IsolatedDesktop:
    def __init__(self,transport:DesktopTransport):
        self.transport=transport; self.reset_id=transport.reset_identity()
        if not self.reset_id:raise UnsafeDesktopBackend("desktop transport did not attest a disposable reset snapshot")
        self.generation=0; self.observations={}; self.closed=False
    def observe(self):
        if self.closed:raise RuntimeError("desktop session is closed")
        pixels,meta=self.transport.screenshot(); required={"pixel_width","pixel_height","guest_width","guest_height","origin_x","origin_y","window_id","display_id","dpi_scale"}
        if not required<=set(meta):raise ValueError("incomplete desktop observation metadata")
        self.generation+=1; ident=f"desktop_obs_{uuid.uuid4().hex[:16]}"; observation={**meta,"observation_id":ident,"generation":self.generation,"reset_id":self.reset_id,"timestamp":time.time(),"screenshot_sha256":hashlib.sha256(pixels).hexdigest()}; self.observations[ident]=observation; return observation
    def act(self,observation_id,kind,*,x=None,y=None,text=None):
        observation=self.observations.get(observation_id)
        if not observation or observation["generation"]!=self.generation:raise StaleDesktopObservation("desktop observation is stale")
        if self.transport.reset_identity()!=self.reset_id:raise StaleDesktopObservation("VM reset identity changed")
        payload={"kind":kind,"window_id":observation["window_id"],"display_id":observation["display_id"],"observation_id":observation_id}
        if x is not None or y is not None:
            transform=DisplayTransform(**{key:observation[key] for key in ("pixel_width","pixel_height","guest_width","guest_height","origin_x","origin_y")}); payload["x"],payload["y"]=transform.to_guest(float(x),float(y))
        if text is not None:payload["text"]=str(text)
        result=self.transport.action(payload); self.generation+=1
        if not result.get("accepted"):raise RuntimeError("guest rejected desktop action")
        return result
    def close(self):self.closed=True
