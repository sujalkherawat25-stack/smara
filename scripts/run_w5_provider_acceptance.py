"""Run the sealed W5 24x3 pack through the canonical provider-backed agent."""
from __future__ import annotations

import argparse
import getpass
import json
import re
import sys
import tempfile
import time
from pathlib import Path

from smara.autonomous_agent import SmaraAutonomousAgent
from smara.harness import Budget, SessionEngine
from smara.output_validation import validate_csv, validate_json, validate_report

ROOT=Path(__file__).parents[1]
PACK_PATH=ROOT/"tests/evals/windows_acceptance/manifest.json"
REF_PATH=ROOT/"tests/evals/windows_acceptance/references.json"
EVIDENCE_PATH=ROOT/"release/evidence/W5_PROVIDER_ACCEPTANCE_2026-09-12.json"
BASE_URL="https://api.sarvam.ai/v2"
MODEL="glm5.3-flash"
MAX_RUPEES=150.0
MAX_SECONDS=90*60
OUTPUT_RUPEES_PER_M=45.0


def save(report:dict)->None:
    temp=EVIDENCE_PATH.with_suffix(".tmp")
    temp.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding="utf-8")
    temp.replace(EVIDENCE_PATH)


def make_agent(root:Path,ident:str,key:str,profile:str,calls:int,contract:dict|None=None):
    session=SessionEngine(root,ident,budget=Budget(600,40,calls,500_000,4),constrained=False)
    if contract:session.set("output_contract",contract)
    agent=SmaraAutonomousAgent(api_key=key,base_url=BASE_URL,model=MODEL,auth_header="api-subscription-key",workspace_root=root,profile=profile,session_engine=session,max_iterations=calls)
    return agent,session


def research_prompt(item:dict)->str:
    evidence=item["input"]["evidence"];claim=item["input"]["claim"]
    return (
        f"Use canonical research tools only on evidence.txt. Plan one node for this claim: {claim} "
        "Ingest evidence.txt and resolve that original claim using the returned evidence ID. "
        f"Then validate the supported evidence statement exactly: {evidence} using the same evidence ID. "
        "If the original resolution is insufficient, call research_validate with require_complete false. "
        "Do not browse. Finish with the validated evidence statement and one line FINAL LABEL: supported, "
        "refuted, or insufficient according to the resolution."
    )


def local_setup(root:Path,item:dict):
    kind=item["input"]["kind"]
    if kind=="json":
        return "coding",7,{"artifacts":[{"path":"answer.json","kind":"json","checks":{"expected":{"value":42}}}]},"Create answer.json containing exactly {\"value\": 42}. Use file_write, verify with file_read, then finish. Do not install packages."
    if kind=="csv":
        return "coding",7,{"artifacts":[{"path":"answer.csv","kind":"csv","checks":{"expected_rows":[{"name":"alpha","value":"42"}]}}]},"Create answer.csv with header name,value and one row alpha,42. Verify it, then finish. Do not install packages."
    if kind=="report":
        return "coding",7,{"artifacts":[{"path":"report.md","kind":"report","checks":{"required_phrases":["verified"],"minimum_words":4}}]},"Create report.md containing a concise result of at least four words including verified. Verify it, then finish."
    if kind=="process":
        return "coding",7,None,"Use process_start to run Python that prints exactly terminal-ok, poll it to completion, confirm exit code zero and output, then finish."
    if kind=="unicode":
        return "coding",7,{"artifacts":[{"path":"ü space.txt","kind":"report","checks":{"required_phrases":["नमस्ते"],"minimum_words":1}}]},"Create the UTF-8 file named ü space.txt containing exactly नमस्ते. Read it back, then finish."
    (root/"owned.txt").write_text("user-owned",encoding="utf-8")
    return "coding",6,None,"Inspect owned.txt. Preserve its user-owned content exactly; do not modify it. Report the preserved value and finish."


def browser_fixture(root:Path)->Path:
    second=root/"second.html";second.write_text("<p>beta</p>",encoding="utf-8")
    page=root/"fixture.html"
    page.write_text(
        f"<p id='value'>alpha</p><form onsubmit=\"const v=document.querySelector('input').value;document.body.dataset.saved=v;document.querySelector('#state').textContent='Saved: '+v;return false\"><input><button>Save</button></form><p id='state'>Not saved</p><a target='_blank' href='{second.as_uri()}'>Tab</a><a download='x.txt' href='data:text/plain,payload-42'>Download</a>",encoding="utf-8")
    return page


def browser_setup(root:Path,item:dict):
    page=browser_fixture(root);kind=item["input"]["kind"]
    base=f"Open {page.as_uri()} with browser_open. "
    if kind=="observe":return 6,None,base+"Observe and confirm alpha is visible, then finish."
    if kind=="form":return 8,None,base+"Fill the input with saved, click Save using fresh DOM references, observe Saved: saved, then finish."
    if kind=="tabs":return 8,None,base+"Click Tab using a fresh reference, list tabs, switch to the new tab, observe beta, then finish."
    if kind=="download":return 8,{"artifacts":[{"path":"download.txt","kind":"report","checks":{"required_phrases":["payload-42"],"minimum_words":1}}]},base+"Download the Download link to download.txt, read it back, confirm payload-42, then finish."
    return 7,None,base+"Confirm the page opened, close the owned browser with browser_close, confirm it closed, then finish."


def mixed_setup(root:Path,item:dict):
    inp=item["input"];(root/"evidence.txt").write_text(inp["evidence"],encoding="utf-8")
    page=browser_fixture(root);output=inp["output"]
    if output.endswith(".json"):contract={"artifacts":[{"path":output,"kind":"json","checks":{"expected":{"validated":True}}}]};instruction=f"write {output} exactly as {{\"validated\": true}}"
    elif output.endswith(".csv"):contract={"artifacts":[{"path":output,"kind":"csv","checks":{"expected_rows":[{"status":"green"}]}}]};instruction=f"write {output} with header status and row green"
    else:contract={"artifacts":[{"path":output,"kind":"report","checks":{"required_phrases":["verified"],"minimum_words":4}}]};instruction=f"write {output} as a four-word-or-longer report containing verified"
    prompt=(f"Do not use the todo tool. Open {page.as_uri()} and observe alpha. Then use canonical research tools: plan one node for {inp['claim']} ingest evidence.txt, resolve it, and validate the supported statement exactly {inp['evidence']} with the evidence ID. Finally {instruction}, verify it, and finish with the validated statement. Do not browse the web or install packages.")
    return 24,contract,prompt


def validate_case(root:Path,item:dict,agent:SmaraAutonomousAgent,result:dict)->bool:
    category=item["category"];inp=item["input"];answer=str(result.get("answer") or "").lower()
    if category=="research":return re.search(rf"\b{re.escape(str(item['_expected']).lower())}\b",answer) is not None
    if category=="local":
        kind=inp["kind"]
        if kind=="json":return validate_json(root/"answer.json",expected={"value":42}).passed
        if kind=="csv":return validate_csv(root/"answer.csv",expected_rows=[{"name":"alpha","value":"42"}]).passed
        if kind=="report":return validate_report(root/"report.md",required_phrases=["verified"],minimum_words=4).passed
        if kind=="process":return "terminal-ok" in " ".join(str(x.get("observation", "")) for x in result.get("trace",[]))
        if kind=="unicode":return (root/"ü space.txt").read_bytes()=="नमस्ते".encode()
        return (root/"owned.txt").read_text(encoding="utf-8")=="user-owned"
    if category=="browser":
        kind=inp["kind"]
        if kind=="download":return (root/"download.txt").read_text(encoding="utf-8")=="payload-42"
        if kind=="cancel":return agent._browser.browser_session_id is None
        sid=agent._browser.browser_session_id
        if not sid:return False
        if kind=="form":return agent._browser.backend.page(agent._browser.backend.sessions[sid]).locator("body").get_attribute("data-saved")=="saved"
        if kind=="tabs":return len(agent._browser.backend.tabs(sid))==2 and "beta" in agent._browser.backend.page(agent._browser.backend.sessions[sid]).locator("body").inner_text()
        return "alpha" in agent._browser.backend.page(agent._browser.backend.sessions[sid]).locator("body").inner_text()
    if category=="mixed":
        output=inp["output"]
        if output.endswith(".json"):return validate_json(root/output,expected={"validated":True}).passed
        if output.endswith(".csv"):return validate_csv(root/output,expected_rows=[{"status":"green"}]).passed
        return validate_report(root/output,required_phrases=["verified"],minimum_words=4).passed
    if inp["kind"]=="compaction":
        events=agent.session_engine.inspect()["events"];ids=[x["payload"].get("continuation_artifact_id") for x in events if x["type"]=="checkpoint" and x["payload"].get("continuation_artifact_id")]
        return len(set(ids))>=3
    time.sleep(1.2);return not (root/"orphan.txt").exists()


def execute(item:dict,repeat:int,key:str)->dict:
    started=time.monotonic()
    with tempfile.TemporaryDirectory(prefix=f"smara-w5-{item['id']}-{repeat}-",ignore_cleanup_errors=True) as raw:
        root=Path(raw);category=item["category"]
        if category=="research":
            (root/"evidence.txt").write_text(item["input"]["evidence"],encoding="utf-8");profile,calls,contract,prompt="research",9,None,research_prompt(item)
        elif category=="local":profile,calls,contract,prompt=local_setup(root,item)
        elif category=="browser":calls,contract,prompt=browser_setup(root,item);profile="full"
        elif category=="mixed":calls,contract,prompt=mixed_setup(root,item);profile="full"
        elif item["input"]["kind"]=="compaction":profile,calls,contract,prompt="coding",12,{"artifacts":[{"path":"step3.txt","kind":"report","checks":{"required_phrases":["constraint-✓"],"minimum_words":1}}]},"Do not use the todo tool. Preserve the exact constraint constraint-✓. Perform at least three distinct steps: write step1.txt containing constraint-✓, read it, write step2.txt containing constraint-✓, read it, write step3.txt containing constraint-✓, read it, then finish by repeating constraint-✓."
        else:profile,calls,contract,prompt="coding",8,None,f"Start a background Python process using {sys.executable!r} that sleeps one second and then writes orphan.txt. Immediately cancel that owned process, confirm cancellation, wait long enough to establish it cannot write the canary, then finish."
        agent,session=make_agent(root,f"{item['id']}-r{repeat}",key,profile,calls,contract)
        try:
            result=agent.run(prompt,max_iterations=calls);validated=validate_case(root,item,agent,result)
            usage=result.get("session",{}).get("usage",{})
            return {"case":item["id"],"category":category,"repeat":repeat,"status":result.get("status"),"completed":bool(result.get("completed")),"validated":bool(validated),"passed":bool(validated and result.get("completed")),"iterations":result.get("iterations"),"usage":usage,"elapsed_seconds":round(time.monotonic()-started,3),"unresolved":result.get("session",{}).get("unresolved_items",[])}
        finally:
            agent._browser.shutdown();session.close()


def main()->int:
    parser=argparse.ArgumentParser();parser.add_argument("--resume",action="store_true");args=parser.parse_args()
    key=getpass.getpass("Temporary Sarvam API key: ").strip()
    if not key:raise SystemExit("A temporary key is required")
    pack=json.loads(PACK_PATH.read_text(encoding="utf-8"));refs=json.loads(REF_PATH.read_text(encoding="utf-8"))["answers"]
    report={"schema_version":1,"provider":"sarvam","model":MODEL,"endpoint":f"{BASE_URL}/chat/completions","scope":"canonical 24x3 W5 acceptance","limits":{"rupees":MAX_RUPEES,"seconds":MAX_SECONDS,"cost_method":"conservative all tokens priced at output rate"},"runs":[],"started_at_epoch":time.time()}
    if args.resume and EVIDENCE_PATH.exists():
        report=json.loads(EVIDENCE_PATH.read_text(encoding="utf-8"))
        # Every completed attempt stays in the denominator. A run is persisted
        # only after its validator returns, so absent case/repetition pairs are
        # the only work that may be resumed.
    done={(x["case"],x["repeat"]) for x in report["runs"]};started=time.monotonic()
    for repeat in range(1,pack["repetitions"]+1):
        for raw_item in pack["tasks"]:
            if (raw_item["id"],repeat) in done:continue
            total_tokens=sum(int(x.get("usage",{}).get("billed_tokens",0)) for x in report["runs"])
            conservative_cost=total_tokens*OUTPUT_RUPEES_PER_M/1_000_000
            if conservative_cost>=MAX_RUPEES or time.monotonic()-started>=MAX_SECONDS:
                report["stopped"]="cost_limit" if conservative_cost>=MAX_RUPEES else "time_limit";save(report);return 2
            item={**raw_item,"_expected":refs[raw_item["id"]]};outcome=execute(item,repeat,key);report["runs"].append(outcome)
            report["updated_at_epoch"]=time.time();save(report);print(json.dumps(outcome,ensure_ascii=False),flush=True)
    tokens=sum(int(x.get("usage",{}).get("billed_tokens",0)) for x in report["runs"])
    report["summary"]={"passed":sum(x["passed"] for x in report["runs"]),"attempted":len(report["runs"]),"billed_tokens":tokens,"conservative_rupees":round(tokens*OUTPUT_RUPEES_PER_M/1_000_000,2),"elapsed_seconds":round(time.monotonic()-started,3)};save(report)
    return 0 if report["summary"]["passed"]==72 else 1


if __name__=="__main__":raise SystemExit(main())
