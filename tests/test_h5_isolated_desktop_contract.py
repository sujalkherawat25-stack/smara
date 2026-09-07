import pytest
from smara.isolated_desktop import DisplayTransform,IsolatedDesktop,StaleDesktopObservation,UnsafeDesktopBackend

class VM:
    def __init__(self):self.reset="snapshot-1";self.actions=[]
    def reset_identity(self):return self.reset
    def screenshot(self):return b"pixels",{"pixel_width":1000,"pixel_height":500,"guest_width":2000,"guest_height":1000,"origin_x":100,"origin_y":50,"window_id":"win","display_id":"display","dpi_scale":2.0}
    def action(self,payload):self.actions.append(payload);return {"accepted":True,"state":"changed"}

@pytest.mark.parametrize("case",range(1,21))
def test_d01_d20_vm_observe_action_contract(case):
    vm=VM();desktop=IsolatedDesktop(vm);obs=desktop.observe();result=desktop.act(obs["observation_id"],"click",x=case,y=case)
    assert result["state"]=="changed" and vm.actions[-1]["x"]==100+case*2
    with pytest.raises(StaleDesktopObservation):desktop.act(obs["observation_id"],"click",x=1,y=1)

def test_coordinate_bounds_and_reset_identity():
    transform=DisplayTransform(100,100,200,300,10,20);assert transform.to_guest(50,50)==(110,170)
    with pytest.raises(ValueError):transform.to_guest(100,0)
    vm=VM();desktop=IsolatedDesktop(vm);obs=desktop.observe();vm.reset="snapshot-2"
    with pytest.raises(StaleDesktopObservation):desktop.act(obs["observation_id"],"type",text="x")

def test_unattested_host_like_transport_is_rejected():
    vm=VM();vm.reset=""
    with pytest.raises(UnsafeDesktopBackend):IsolatedDesktop(vm)
