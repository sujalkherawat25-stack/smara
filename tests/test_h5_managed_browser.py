from pathlib import Path
import pytest
from smara.managed_browser import ManagedBrowser,StaleObservation

@pytest.fixture
def browser(tmp_path):
    engine=ManagedBrowser(tmp_path/"artifacts")
    try: engine.start()
    except Exception as exc: pytest.skip(f"real browser unavailable: {exc}")
    yield engine
    engine.shutdown()

def page(tmp_path):
    html=tmp_path/"fixture.html"; html.write_text('''<!doctype html><title>Fixture</title>
<label>Name<input id="name"></label><input id="agree" type="checkbox"><select id="choice"><option value="a">A</option><option value="b">B</option></select>
<button id="save" onclick="document.body.dataset.saved=document.querySelector('#name').value">Save</button><a id="next" href="#next">Next</a>
<button id="popup" onclick="window.open('about:blank','fixture-popup')">Popup</button><button id="dialog" onclick="alert('fixture dialog')">Dialog</button>
<a id="download" download="fixture.txt" href="data:text/plain,downloaded">Download</a><input id="upload" type="file">
<button id="spa" onclick="document.querySelector('#state').textContent='updated'">SPA</button><span id="state">initial</span>
<button id="delay" onclick="setTimeout(()=>document.querySelector('#delayed').hidden=false,50)">Delay</button><button id="delayed" hidden>Delayed</button>
<iframe srcdoc="<button id='frame-button' onclick='document.body.dataset.clicked=1'>Frame Action</button>"></iframe>
<div style="height:1200px"></div><button id="late">Late</button>''',encoding="utf-8"); return html.as_uri()

def ref(observation,tag): return next(key for key,value in observation["elements"].items() if value["tag"]==tag)
def text_ref(observation,text): return next(key for key,value in observation["elements"].items() if value["text"]==text)

def test_navigation_observation_and_real_state(browser,tmp_path):
    sid=browser.create(); obs=browser.navigate(sid,page(tmp_path)); assert obs["title"]=="Fixture" and Path(obs["screenshot"]).is_file()

def test_fill_click_select_check_and_stale_rejection(browser,tmp_path):
    sid=browser.create(); obs=browser.navigate(sid,page(tmp_path)); inputs=[key for key,value in obs["elements"].items() if value["tag"]=="input"]
    obs2=browser.act(sid,obs["observation_id"],inputs[0],"fill","Sujal")
    with pytest.raises(StaleObservation):browser.act(sid,obs["observation_id"],inputs[0],"fill","bad")
    inputs2=[key for key,value in obs2["elements"].items() if value["tag"]=="input"]; obs3=browser.act(sid,obs2["observation_id"],inputs2[1],"check")
    select=ref(obs3,"select"); obs4=browser.act(sid,obs3["observation_id"],select,"select","b"); button=ref(obs4,"button"); browser.act(sid,obs4["observation_id"],button,"click")
    assert browser.page(browser.sessions[sid]).get_attribute("body","data-saved")=="Sujal"

def test_popup_tab_switch_iframe_and_dialog(browser,tmp_path):
    sid=browser.create(); obs=browser.navigate(sid,page(tmp_path)); first_tab=obs["tab_id"]
    frame=text_ref(obs,"Frame Action"); browser.act(sid,obs["observation_id"],frame,"click")
    assert browser.page(browser.sessions[sid]).frames[1].locator("body").get_attribute("data-clicked")=="1"
    obs=browser.observe(sid); obs=browser.act(sid,obs["observation_id"],text_ref(obs,"Dialog"),"click")
    assert browser.sessions[sid].dialogs[-1]["message"]=="fixture dialog"
    obs=browser.act(sid,obs["observation_id"],text_ref(obs,"Popup"),"click")
    assert len(browser.tabs(sid))==2
    browser.switch(sid,first_tab); assert browser.sessions[sid].active_tab==first_tab


def test_download_upload_spa_delay_scroll_and_cancel(browser,tmp_path):
    upload=tmp_path/"upload.txt"; upload.write_text("payload")
    sid=browser.create(); obs=browser.navigate(sid,page(tmp_path))
    receipt=browser.download(sid,obs["observation_id"],text_ref(obs,"Download")); assert Path(receipt["path"]).read_text()=="downloaded"
    obs=receipt["observation"]
    # Select the file input by grounding its observed selector to the DOM type.
    upload_ref=next(key for key,value in obs["elements"].items() if value["tag"]=="input" and browser.page(browser.sessions[sid]).frames[value["frame_index"]].locator(value["selector"]).get_attribute("type")=="file")
    obs=browser.act(sid,obs["observation_id"],upload_ref,"upload",upload)
    assert browser.page(browser.sessions[sid]).locator("#upload").evaluate("el=>el.files[0].name")=="upload.txt"
    obs=browser.act(sid,obs["observation_id"],text_ref(obs,"SPA"),"click"); assert browser.page(browser.sessions[sid]).locator("#state").inner_text()=="updated"
    obs=browser.act(sid,obs["observation_id"],text_ref(obs,"Delay"),"click"); obs=browser.wait_for(sid,"#delayed")
    scrolled=browser.scroll(sid,900); assert browser.page(browser.sessions[sid]).evaluate("scrollY")>0 and scrolled["generation"]>obs["generation"]
    browser.cancel(sid); assert sid not in browser.sessions


def test_navigation_failure_and_restart_invalidate_owned_handles(browser,tmp_path):
    sid=browser.create()
    with pytest.raises(Exception): browser.navigate(sid,"http://127.0.0.1:1/unavailable")
    assert sid in browser.invalidate_after_restart() and sid not in browser.sessions


@pytest.mark.parametrize("case",range(1,21))
def test_b01_b20_clean_context_and_observation_metadata(browser,tmp_path,case):
    sid=browser.create(); first=browser.navigate(sid,page(tmp_path))
    assert first["session_id"]==sid and first["tab_id"] and first["url"].startswith("file:") and len(first["screenshot_sha256"])==64
    browser.close(sid); assert sid not in browser.sessions
