from scripts.audit_repository_secrets import scan
from scripts.audit_repository_secrets import verify_public_fixture_origin
from types import SimpleNamespace
import json


def test_secrets_are_identified_but_never_reproduced():
    value = "sk_mem_" + "z2Q7" * 12
    findings = scan(("key=" + value).encode(), "synthetic.txt", "fixture")
    assert findings[0]["rule"] == "smara_provider_key"
    assert findings[0]["value"] == "REDACTED"
    assert value not in str(findings)


def test_known_sample_values_remain_findings_not_auto_dismissed():
    key = "ASIA" + "E" * 16
    assert len(scan(key.encode(), "tests/fixture.py", "fixture")) == 1
    assert scan(b"api_key=os.getenv('API_KEY')", "source.py", "fixture") == []


def test_gate_does_not_exempt_new_secrets_in_test_or_vendor_files(tmp_path, monkeypatch):
    (tmp_path / "native").mkdir()
    (tmp_path / "native/UPSTREAM.json").write_text(json.dumps({"commit": "synthetic-revision"}))
    monkeypatch.setattr("scripts.audit_repository_secrets.subprocess.run", lambda *args, **kwargs: SimpleNamespace(stdout=b'{"truncated":false,"tree":[]}'))
    entries = scan(("sk_mem_" + "z2Q7" * 12).encode(), "tests/test_production_skills_and_plugins.py", "fixture", "new-blob")
    result = {"findings": entries}
    verify_public_fixture_origin(tmp_path, result)
    assert result["unclassified_findings"]
    assert "require review" in result["status"]
