from __future__ import annotations
import argparse,json,sys,tempfile,time
from pathlib import Path
from smara.harness import Budget,SessionEngine,ToolCall,_LIVE_PROCESSES

def main():
    parser=argparse.ArgumentParser();parser.add_argument("--seconds",type=int,default=1800);parser.add_argument("--output",required=True);args=parser.parse_args()
    started=time.time();actions=interruptions=0;max_live=0
    with tempfile.TemporaryDirectory(prefix="smara-w3-soak-") as raw:
        root=Path(raw);engine=SessionEngine(root,"soak",budget=Budget(args.seconds+120,10000,10000,1000000,1),constrained=False);engine.begin_incremental("deterministic multi-action soak")
        while time.time()-started<args.seconds:
            path="state.json";content=json.dumps({"action":actions,"unicode":"✓"})
            result=engine.broker.dispatch(ToolCall(f"w{actions}","write_file",{"path":path,"content":content},str(root)));assert result.ok
            result=engine.broker.dispatch(ToolCall(f"p{actions}","run_process",{"argv":[sys.executable,"-c","print('verified')"],"cwd":".","timeout_seconds":10},str(root)));assert result.ok and "verified" in result.text
            engine.checkpoint([{"role":"tool","content":"verified"}],{"constraints":["no orphan processes","preserve Unicode ✓"],"next_action":"continue"})
            engine.close();engine=SessionEngine(root,"soak",constrained=False);assert json.loads((root/path).read_text())["action"]==actions;interruptions+=1
            max_live=max(max_live,sum(proc.poll() is None for proc in _LIVE_PROCESSES.values()));actions+=1
            time.sleep(min(5,max(0,args.seconds-(time.time()-started))))
        engine.close();orphans=[ident for ident,proc in _LIVE_PROCESSES.items() if proc.poll() is None]
    payload={"version":1,"duration_seconds":round(time.time()-started,3),"requested_seconds":args.seconds,"actions":actions,"forced_reopens":interruptions,"max_live_processes":max_live,"orphan_processes":orphans,"passed":not orphans and time.time()-started>=args.seconds}
    target=Path(args.output);target.parent.mkdir(parents=True,exist_ok=True);target.write_text(json.dumps(payload,indent=2),encoding="utf-8");print(json.dumps(payload));return 0 if payload["passed"] else 1
if __name__=="__main__":raise SystemExit(main())
