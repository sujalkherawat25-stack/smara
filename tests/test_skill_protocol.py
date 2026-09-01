from __future__ import annotations

import copy

import pytest

from smara.skill_protocol import (
    SKILL_MANIFEST_SCHEMA,
    SkillManifest,
    SkillRegistry,
    validate_skill_manifest,
)


def _manifest() -> dict:
    return {
        "schema_version": SKILL_MANIFEST_SCHEMA,
        "name": "workspace-health",
        "version": "1.0.0",
        "description": "Inspect a workspace and report its bounded health.",
        "inputs": [{"name": "root", "type": "string", "description": "Approved workspace root"}],
        "outputs": [{"name": "report", "type": "object", "description": "Bounded health report"}],
        "permissions": {"capabilities": ["local_file_read"], "connectors": [], "approved_domains": []},
        "risk": "safe",
        "provider": "smara",
        "owner": "acct_test",
        "stages": [
            {
                "id": "inspect",
                "tool": "local_file_read",
                "operation": "tree",
                "arguments": {"path": "$input.root"},
            },
            {
                "id": "report",
                "tool": "local_file_read",
                "operation": "metadata",
                "depends_on": ["inspect"],
                "arguments": {"path": "$input.root"},
            },
        ],
        "tests": [{"name": "empty-workspace", "inputs": {"root": "workspace"}, "expected_status": "completed"}],
        "rollback": {"strategy": "none"},
    }


def test_manifest_is_versioned_bounded_and_fingerprinted():
    manifest = validate_skill_manifest(_manifest())
    assert isinstance(manifest, SkillManifest)
    assert manifest.schema_version == "smara.skill.v1"
    assert len(manifest.fingerprint()) == 64
    assert manifest.fingerprint() == SkillManifest.model_validate(_manifest()).fingerprint()


def test_manifest_rejects_unknown_fields_secrets_and_executable_payloads():
    unknown = _manifest()
    unknown["unexpected"] = True
    with pytest.raises(RuntimeError, match="Invalid skill manifest"):
        validate_skill_manifest(unknown)

    secret = _manifest()
    secret["stages"][0]["arguments"] = {"api_key": "not-allowed"}
    with pytest.raises(RuntimeError, match="credentials"):
        validate_skill_manifest(secret)

    executable = _manifest()
    executable["stages"][0]["arguments"] = {"script": "python do_bad_thing.py"}
    with pytest.raises(RuntimeError, match="executable"):
        validate_skill_manifest(executable)


def test_manifest_rejects_cycles_unknown_dependencies_and_missing_permissions():
    cyclic = _manifest()
    cyclic["stages"][0]["depends_on"] = ["report"]
    with pytest.raises(RuntimeError, match="acyclic"):
        validate_skill_manifest(cyclic)

    unknown_dependency = _manifest()
    unknown_dependency["stages"][1]["depends_on"] = ["missing"]
    with pytest.raises(RuntimeError, match="unknown stage"):
        validate_skill_manifest(unknown_dependency)

    missing_permission = _manifest()
    missing_permission["permissions"]["capabilities"] = []
    with pytest.raises(RuntimeError, match="permissions"):
        validate_skill_manifest(missing_permission)


def test_confirm_stage_requires_confirm_risk():
    manifest = _manifest()
    manifest["stages"][0]["requires_approval"] = True
    with pytest.raises(RuntimeError, match="risk=confirm"):
        validate_skill_manifest(manifest)


def test_registry_requires_passed_test_before_publish_and_fails_closed_on_change():
    registry = SkillRegistry()
    record = registry.register(_manifest())
    assert record.state == "draft"
    with pytest.raises(RuntimeError, match="disposable test"):
        registry.publish("workspace-health", "1.0.0", approved_by="acct_test")

    tested = registry.record_test("workspace-health", "1.0.0", passed=True, run_id="run-001")
    assert tested.state == "tested"
    published = registry.publish("workspace-health", "1.0.0", approved_by="acct_test")
    assert published.state == "published"
    assert registry.assert_runnable("workspace-health", "1.0.0").state == "published"

    changed = copy.deepcopy(_manifest())
    changed["description"] = "Changed after approval"
    with pytest.raises(RuntimeError, match="changed after approval"):
        registry.assert_runnable("workspace-health", "1.0.0", changed)

    deprecated = registry.deprecate("workspace-health", "1.0.0")
    assert deprecated.state == "deprecated"
    with pytest.raises(RuntimeError, match="not published"):
        registry.assert_runnable("workspace-health", "1.0.0")


def test_registry_detaches_nested_manifest_mutations():
    registry = SkillRegistry()
    registry.register(_manifest())
    registry.record_test("workspace-health", "1.0.0", passed=True, run_id="run-002")
    registry.publish("workspace-health", "1.0.0", approved_by="acct_test")
    returned = registry.get("workspace-health", "1.0.0")
    returned.manifest.stages[0].arguments["path"] = "changed-locally"
    assert registry.assert_runnable("workspace-health", "1.0.0").state == "published"


def test_failed_test_keeps_skill_in_draft_and_duplicate_registration_is_rejected():
    registry = SkillRegistry()
    registry.register(_manifest())
    failed = registry.record_test("workspace-health", "1.0.0", passed=False, run_id="run-fail")
    assert failed.state == "draft"
    with pytest.raises(RuntimeError, match="already registered"):
        registry.register(_manifest())
