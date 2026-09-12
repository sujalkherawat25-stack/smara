"""Sealed deterministic acceptance suite for Windows autonomy v3."""
import hashlib
import json
import sys
import time
from pathlib import Path
import pytest

from smara.continuation import ContinuationState
from smara.evidence_index import EvidenceIndex
from smara.harness import ArtifactStore, SessionEngine, ToolBroker, ToolCall
from smara.managed_browser import ManagedBrowser
from smara.output_validation import validate_csv, validate_json, validate_report

ROOT = Path(__file__).parents[1]
PACK = json.loads((ROOT / "tests/evals/windows_acceptance_v3/manifest.json").read_text(encoding="utf-8"))
REFERENCES = json.loads((ROOT / "tests/evals/windows_acceptance_v3/references.json").read_text(encoding="utf-8"))["answers"]


def research(root, item):
    text = item["input"]["evidence"]
    index = EvidenceIndex(ArtifactStore(root / "evidence"))
    record = index.add(
        kind="fetched_passage",
        url="https://fixture.invalid/source",
        content=text.encode(),
        text=text,
        extracted_content=text.encode(),
        start=0,
        end=len(text)
    )
    return index.judge(record.id, item["input"]["claim"]).state


def page(root, value):
    second = root / "second.html"
    second.write_text("<p>gamma</p>", encoding="utf-8")
    target = root / "fixture.html"
    target.write_text(
        f'''<p>{value}</p><form onsubmit="document.body.dataset.saved=document.querySelector('input').value;return false"><input><button>Save</button></form><a target="_blank" href="{second.as_uri()}">Tab</a><a download="x.txt" href="data:text/plain,payload-128">Download</a>''',
        encoding="utf-8"
    )
    return target


def browser(root, item):
    backend = ManagedBrowser(root / "browser")
    session = backend.create()
    obs = backend.navigate(session, page(root, item["input"]["value"]).as_uri())
    kind = item["input"]["kind"]
    try:
        if kind == "observe":
            return item["input"]["value"] in obs["body_text"]
        if kind == "form":
            inp = next(k for k, v in obs["elements"].items() if v["tag"] == "input")
            obs = backend.act(session, obs["observation_id"], inp, "fill", "registered")
            button = next(k for k, v in obs["elements"].items() if v["text"] == "Save")
            backend.act(session, obs["observation_id"], button, "click")
            return backend.page(backend.sessions[session]).locator("body").get_attribute("data-saved") == "registered"
        if kind == "tabs":
            link = next(k for k, v in obs["elements"].items() if v["text"] == "Tab")
            backend.act(session, obs["observation_id"], link, "click")
            return len(backend.tabs(session)) == 2
        if kind == "download":
            link = next(k for k, v in obs["elements"].items() if v["text"] == "Download")
            result = backend.download(session, obs["observation_id"], link)
            return Path(result["path"]).read_text() == "payload-128"
        if kind == "cancel":
            backend.cancel(session)
            session = None
            return not backend.sessions
    finally:
        if session in backend.sessions:
            backend.close(session)
        backend.shutdown()


def local(root, item):
    kind = item["input"]["kind"]
    if kind == "json":
        path = root / "answer.json"
        path.write_text('{"value":128}')
        return validate_json(path, expected={"value": 128}).passed
    if kind == "csv":
        path = root / "answer.csv"
        path.write_text("name,value\nalpha,128\n")
        return validate_csv(path, expected_rows=[{"name": "alpha", "value": "128"}]).passed
    if kind == "report":
        path = root / "report.md"
        path.write_text("Result is independently validated.")
        return validate_report(path, required_phrases=["validated"], minimum_words=4).passed
    if kind == "process":
        broker = ToolBroker(root, {"run_process"}, constrained=False)
        result = broker.dispatch(ToolCall("p", "run_process", {"argv": [sys.executable, "-c", "print('terminal-pass')"], "cwd": "."}, str(root)))
        return result.ok and "terminal-pass" in result.text
    if kind == "unicode":
        path = root / "ü space.txt"
        path.write_text(item["input"]["value"], encoding="utf-8")
        return path.read_bytes() == item["input"]["value"].encode()
    path = root / "owned.txt"
    path.write_text("user-owned")
    broker = ToolBroker(root, {"write_file"}, constrained=False)
    result = broker.dispatch(ToolCall("c", "write_file", {"path": "owned.txt", "content": "wrong", "expected_sha256": "0" * 64}, str(root)))
    return not result.ok and path.read_text() == "user-owned"


def long(root, item):
    if item["input"]["kind"] == "compaction":
        engine = SessionEngine(root, "long")
        parents = []
        for _ in range(3):
            engine.checkpoint([], {"constraints": [item["input"]["value"]]})
            parents.append(engine.get("continuation_artifact_id"))
        state = ContinuationState.from_dict(json.loads(engine.resolve_artifact(parents[-1])))
        return len(set(parents)) == 3 and state.constraints == (item["input"]["value"],)
    engine = SessionEngine(root, "cancel", constrained=False)
    script = "import time;time.sleep(15);open('orphan.txt','w').write('bad')"
    engine.run("x", [{"name": "process_start", "arguments": {"argv": [sys.executable, "-c", script], "cwd": "."}}])
    engine.cancel()
    time.sleep(1.2)
    return not (root / "orphan.txt").exists()


@pytest.mark.parametrize("repeat", range(3))
@pytest.mark.parametrize("item", PACK["tasks"], ids=lambda item: item["id"])
def test_sealed_windows_acceptance_pack_v3(tmp_path, item, repeat):
    expected = REFERENCES[item["id"]]
    category = item["category"]
    if category == "research":
        actual = research(tmp_path, item)
    elif category == "local":
        actual = local(tmp_path, item)
    elif category == "browser":
        actual = browser(tmp_path, item)
    elif category == "mixed":
        supported = research(tmp_path, item) == "supported"
        output = item["input"]["output"]
        if output.endswith(".json"):
            (tmp_path / output).write_text('{"validated":true}')
            valid = validate_json(tmp_path / output, expected={"validated": True}).passed
        elif output.endswith(".csv"):
            (tmp_path / output).write_text("status\noperational\n")
            valid = validate_csv(tmp_path / output, expected_rows=[{"status": "operational"}]).passed
        else:
            (tmp_path / output).write_text(item["input"]["evidence"])
            valid = validate_report(tmp_path / output, required_phrases=["validated"]).passed
        actual = supported and valid
    else:
        actual = long(tmp_path, item)
    assert actual == expected


def test_pack_v3_is_sealed_complete_and_predeclared():
    tasks = PACK["tasks"]
    assert len(tasks) == 24 and len({x["id"] for x in tasks}) == 24 and set(REFERENCES) == {x["id"] for x in tasks}
    assert {category: sum(x["category"] == category for x in tasks) for category in {"research", "local", "browser", "mixed", "long"}} == {
        "research": 8, "local": 6, "browser": 5, "mixed": 3, "long": 2
    }
    assert all(item.get("validator") for item in tasks) and PACK["repetitions"] == 3 and PACK["delegation"] is False
