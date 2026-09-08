import pytest
from smara.isolated_desktop import DesktopDeadlineExceeded,DisplayTransform,IsolatedDesktop,StaleDesktopObservation,UnsafeDesktopBackend

class VM:
    def __init__(self):self.reset="snapshot-1";self.actions=[];self.cancelled=False
    def reset_identity(self):return self.reset
    def screenshot(self):return b"pixels",{"pixel_width":1000,"pixel_height":500,"guest_width":2000,"guest_height":1000,"origin_x":100,"origin_y":50,"window_id":"win","display_id":"display","dpi_scale":2.0}
    def action(self,payload):self.actions.append(payload);return {"accepted":True,"state":"changed"}
    def cancel(self):self.cancelled=True

@pytest.mark.parametrize("case",range(1,21))
def test_d01_d20_vm_observe_action_contract(case):
    vm=VM();desktop=IsolatedDesktop(vm);obs=desktop.observe()
    actions={1:("launch",{"text":"fixture.exe"}),2:("focus",{"text":"fixture"}),3:("type",{"text":"hello"}),4:("hotkey",{"text":"CTRL+S"}),5:("click",{"x":case,"y":case}),6:("drag",{"x":1,"y":1,"end_x":5,"end_y":5}),7:("scroll",{"delta_y":300}),8:("save",{"text":"file.txt"}),9:("open",{"text":"file.txt"}),10:("window_move",{"x":2,"y":2}),11:("resize",{"width":800,"height":600}),12:("dialog",{"text":"accept"}),13:("clipboard",{"text":"scoped"}),14:("click",{"x":50,"y":50}),15:("click",{"x":0,"y":0}),16:("focus",{"text":"occluded-window"})}
    if case<=16:
        kind,arguments=actions[case];result=desktop.act(obs["observation_id"],kind,**arguments);assert result["state"]=="changed" and vm.actions[-1]["kind"]==kind
    elif case==17:
        desktop.act(obs["observation_id"],"click",x=1,y=1)
        with pytest.raises(StaleDesktopObservation):desktop.act(obs["observation_id"],"click",x=1,y=1)
    elif case==18:
        vm.reset="snapshot-2"
        with pytest.raises(StaleDesktopObservation):desktop.act(obs["observation_id"],"focus",text="x")
    elif case==19:
        desktop.deadline=0
        with pytest.raises(DesktopDeadlineExceeded):desktop.act(obs["observation_id"],"focus",text="x")
    else:
        desktop.cancel();assert vm.cancelled
        with pytest.raises(RuntimeError):desktop.act(obs["observation_id"],"focus",text="x")

def test_coordinate_bounds_and_reset_identity():
    transform=DisplayTransform(100,100,200,300,10,20);assert transform.to_guest(50,50)==(110,170)
    with pytest.raises(ValueError):transform.to_guest(100,0)
    vm=VM();desktop=IsolatedDesktop(vm);obs=desktop.observe();vm.reset="snapshot-2"
    with pytest.raises(StaleDesktopObservation):desktop.act(obs["observation_id"],"type",text="x")

def test_unattested_host_like_transport_is_rejected():
    vm=VM();vm.reset=""
    with pytest.raises(UnsafeDesktopBackend):IsolatedDesktop(vm)
