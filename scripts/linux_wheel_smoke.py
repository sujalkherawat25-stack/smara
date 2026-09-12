import sys
from pathlib import Path
from smara.harness import ToolBroker,ToolCall
w=Path("/tmp/smara-wheel-work");w.mkdir(exist_ok=True)
b=ToolBroker(w,{"write_file","read_file","run_process"},constrained=False)
assert b.dispatch(ToolCall("w","write_file",{"path":"x.txt","content":"42"},str(w))).ok
assert b.dispatch(ToolCall("r","read_file",{"path":"x.txt"},str(w))).text=="42"
assert b.dispatch(ToolCall("p","run_process",{"argv":[sys.executable,"-c","print(42)"],"cwd":"."},str(w))).exit_code==0
print("LINUX_WHEEL_SMOKE_OK")
