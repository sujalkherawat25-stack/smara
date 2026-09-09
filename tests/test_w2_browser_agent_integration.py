import json
from pathlib import Path
from urllib.parse import unquote,urlparse

import pytest

from smara.autonomous_agent import SmaraAutonomousAgent
from smara.harness import Budget,SessionEngine
from smara.research import RetrievedSource
from smara.research_tools import SearchHit


class Response:
    def __init__(self,payload,index):self.payload=payload;self.headers={"x-request-id":f"browser-{index}"}
    def __enter__(self):return self
    def __exit__(self,*_):return None
    def read(self):return json.dumps(self.payload).encode()


def tool(name,args,index):
    return {"choices":[{"finish_reason":"tool_calls","message":{"content":"","tool_calls":[{"id":f"{name}-{index}","type":"function","function":{"name":name,"arguments":json.dumps(args)}}]}}],"usage":{"total_tokens":20}}


def final(answer="done"):
    return {"choices":[{"finish_reason":"stop","message":{"content":f"FINAL ANSWER: {answer}"}}],"usage":{"total_tokens":10}}


class Searcher:
    def __init__(self,url):self.url=url
    async def search(self,query,*,max_results=5):return [SearchHit(self.url,"Fixture","The verified browser value is 42 kilograms.","fixture")]


class Fetcher:
    def __init__(self,url,text):self.url=url;self.text=text
    async def fetch(self,url):
        raw=Path(unquote(urlparse(self.url).path.lstrip("/"))).read_bytes() if self.url.startswith("file:///") else self.text.encode()
        return RetrievedSource("Fixture",self.text,"","2026-09-09T00:00:00+00:00",None,raw,self.url,(),"text/html")


def fixture_pages(root:Path):
    second=root/"second.html";second.write_text("<title>Second</title><p>Second tab comparison value: beta</p>",encoding="utf-8")
    main=root/"browser fixture.html";main.write_text(f'''<!doctype html><title>Browser fixture</title>
<p id="fact">The verified browser value is 42 kilograms.</p>
<form onsubmit="document.body.dataset.saved=document.querySelector('#name').value;return false"><input id="name"><button>Submit</button></form>
<a id="second" target="_blank" href="{second.as_uri()}">Compare</a>
<a id="download" download="payload.txt" href="data:text/plain,downloaded-browser-payload">Download data</a>
''',encoding="utf-8")
    return main,second


def make_agent(root:Path,name:str,profile="full"):
    session=SessionEngine(root,name,budget=Budget(180,40,40,800_000,3))
    agent=SmaraAutonomousAgent(api_key="fixture",profile=profile,workspace_root=root,session_engine=session,max_iterations=15)
    return agent,session


def run_script(monkeypatch,agent,script,iterations=15):
    cursor={"value":0}
    def urlopen(*_args,**_kwargs):
        index=cursor["value"];cursor["value"]+=1;return Response(script(agent,index),index)
    monkeypatch.setattr("urllib.request.urlopen",urlopen)
    return agent.run("Complete the deterministic browser fixture journey.",max_iterations=iterations)


def latest(agent):
    session=agent._browser.backend.sessions[agent._browser.browser_session_id]
    return list(session.observations.values())[-1]


def element(observation,*,text=None,tag=None):
    return next(key for key,value in observation["elements"].items() if (text is None or value["text"]==text) and (tag is None or value["tag"]==tag))


@pytest.mark.parametrize("repeat",range(3))
def test_w2_search_open_and_cite_through_canonical_agent(tmp_path,monkeypatch,repeat):
    main,_=fixture_pages(tmp_path);claim="The verified browser value is 42 kilograms.";agent,session=make_agent(tmp_path,f"cite-{repeat}","research")
    agent._research.searcher=Searcher(main.as_uri());agent._research.fetcher=Fetcher(main.as_uri(),claim)
    def script(agent,index):
        evidence_id=next((ident for ident,record in agent._research.index.records.items() if record.kind=="fetched_passage"),"")
        sequence=[tool("research_plan",{"question":"What is the value?","nodes":[{"id":"fact","question":"What is the value?"}]},0),tool("research_search",{"node_id":"fact","query":"fixture value"},1),tool("browser_open",{"url":main.as_uri()},2),tool("research_fetch",{"node_id":"fact","url":main.as_uri()},3),tool("research_resolve",{"node_id":"fact","claim":claim,"evidence_ids":[evidence_id]},4),tool("research_validate",{"claims":[{"claim":claim,"evidence_ids":[evidence_id]}]},5),final(claim)]
        return sequence[min(index,6)]
    try:
        result=run_script(monkeypatch,agent,script)
        assert result["completed"] and latest(agent)["url"]==main.as_uri()
    finally:agent._browser.shutdown()


@pytest.mark.parametrize("repeat",range(3))
def test_w2_multi_tab_comparison_and_switch(tmp_path,monkeypatch,repeat):
    main,second=fixture_pages(tmp_path);agent,session=make_agent(tmp_path,f"tabs-{repeat}");state={}
    def script(agent,index):
        if index==1:
            obs=latest(agent);state["first"]=obs["tab_id"];return tool("browser_act",{"observation_id":obs["observation_id"],"ref":element(obs,text="Compare"),"action":"click"},index)
        if index==3:return tool("browser_switch",{"tab_id":state["first"]},index)
        return [tool("browser_open",{"url":main.as_uri()},0),None,tool("browser_tabs",{},2),None,final("Compared both tabs")][min(index,4)]
    try:
        result=run_script(monkeypatch,agent,script)
        assert result["completed"] and len(agent._browser.backend.tabs(agent._browser.browser_session_id))==2 and latest(agent)["tab_id"]==state["first"]
        assert any(item["url"]==second.as_uri() for item in agent._browser.backend.tabs(agent._browser.browser_session_id))
    finally:agent._browser.shutdown()


@pytest.mark.parametrize("repeat",range(3))
def test_w2_form_submit_has_independently_observable_state(tmp_path,monkeypatch,repeat):
    main,_=fixture_pages(tmp_path);agent,session=make_agent(tmp_path,f"form-{repeat}")
    def script(agent,index):
        if index==1:
            obs=latest(agent);return tool("browser_act",{"observation_id":obs["observation_id"],"ref":element(obs,tag="input"),"action":"fill","value":f"saved-{repeat}"},index)
        if index==2:
            obs=latest(agent);return tool("browser_act",{"observation_id":obs["observation_id"],"ref":element(obs,text="Submit"),"action":"click"},index)
        return [tool("browser_open",{"url":main.as_uri()},0),None,None,final("Form saved")][min(index,3)]
    try:
        result=run_script(monkeypatch,agent,script);page=agent._browser.backend.page(agent._browser.backend.sessions[agent._browser.browser_session_id])
        assert result["completed"] and page.locator("body").get_attribute("data-saved")==f"saved-{repeat}"
    finally:agent._browser.shutdown()


@pytest.mark.parametrize("repeat",range(3))
def test_w2_download_to_local_analysis(tmp_path,monkeypatch,repeat):
    main,_=fixture_pages(tmp_path);agent,session=make_agent(tmp_path,f"download-{repeat}");destination=f"downloads/payload-{repeat}.txt"
    def script(agent,index):
        if index==1:
            obs=latest(agent);return tool("browser_download",{"observation_id":obs["observation_id"],"ref":element(obs,text="Download data"),"destination":destination},index)
        return [tool("browser_open",{"url":main.as_uri()},0),None,tool("file_read",{"file_path":destination},2),final("Downloaded and inspected")][min(index,3)]
    try:
        result=run_script(monkeypatch,agent,script)
        assert result["completed"] and (tmp_path/destination).read_text()=="downloaded-browser-payload"
    finally:agent._browser.shutdown()


@pytest.mark.parametrize("repeat",range(3))
def test_w2_backend_loss_recovery_and_session_cancel(tmp_path,monkeypatch,repeat):
    main,_=fixture_pages(tmp_path);agent,session=make_agent(tmp_path,f"recover-{repeat}")
    def script(agent,index):
        if index==1:
            agent._browser.backend.invalidate_after_restart();return tool("browser_observe",{},index)
        return [tool("browser_open",{"url":main.as_uri()},0),None,tool("browser_open",{"url":main.as_uri()},2),final("Recovered explicitly")][min(index,3)]
    try:
        result=run_script(monkeypatch,agent,script)
        assert result["completed"] and latest(agent)["url"]==main.as_uri()
        session.cancel();assert agent._browser.browser_session_id is None and session.get("browser_handle") is None
    finally:agent._browser.shutdown()
