import hashlib
import json
from pathlib import Path


def test_w1_manifest_definitions_fixture_hash_and_node_ids():
    root=Path(__file__).parents[1]
    manifest=json.loads((root/"tests/evals/windows_research/manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"]==1 and len(manifest["cases"])==17
    fixture=root/manifest["fixture"]
    assert hashlib.sha256(fixture.read_bytes()).hexdigest()==manifest["fixture_sha256"]
    ids=[item["id"] for item in manifest["cases"]]
    assert ids==[f"W1-{index:02d}" for index in range(1,18)]
    source=fixture.read_text(encoding="utf-8")
    for case in manifest["cases"]:
        definition=json.dumps({key:case[key] for key in ("id","fixture","validator","test")},sort_keys=True,separators=(",",":")).encode()
        assert hashlib.sha256(definition).hexdigest()==case["definition_sha256"]
        function=case["test"].split("::",1)[1].split("[",1)[0]
        assert f"def {function}(" in source
