import hashlib
import json
from pathlib import Path

import pytest

from smara.context_packing import ContextOverflow, ModelContextProfile, conservative_tokens, pack_messages
from smara.continuation import ContinuationState
from smara.harness import Budget, SessionEngine


def test_packer_fits_unicode_and_keeps_tool_exchange_whole():
    messages = [{"role":"system","content":"rules","_smara_mandatory":True}]
    for index in range(20):
        messages += [{"role":"assistant","content":"","tool_calls":[{"id":f"c{index}"}]},{"role":"tool","tool_call_id":f"c{index}","content":"🧪"*200}]
    messages.append({"role":"user","content":"current constraint","_smara_mandatory":True})
    profile = ModelContextProfile("utf8-upper", 4000, 500, 100, 20)
    packed = pack_messages(messages, profile, tools=[{"large":"λ"*100}])
    assert packed.input_tokens + profile.output_reserve + profile.safety_margin <= profile.input_capacity
    call_ids = {tc["id"] for item in packed.messages for tc in item.get("tool_calls",[])}
    assert all(item.get("tool_call_id") in call_ids for item in packed.messages if item.get("role")=="tool")
    assert packed.messages[-1]["content"] == "current constraint"


def test_mandatory_state_overflow_is_recoverable_error():
    profile=ModelContextProfile("bound",100,30,20,10)
    with pytest.raises(ContextOverflow): pack_messages([{"role":"user","content":"x"*100,"_smara_mandatory":True}],profile)


def test_continuation_round_trip_preserves_evidence_and_budget():
    state=ContinuationState(objective="repair",constraints=("keep api",),passing_evidence_ids=("sha",),usage={"tool_calls":3},next_action="test")
    restored=ContinuationState.from_dict(json.loads(json.dumps(state.to_dict())))
    assert restored.objective==state.objective
    assert tuple(restored.passing_evidence_ids)==("sha",)
    assert restored.usage["tool_calls"]==3


def test_three_checkpoints_form_hash_verified_lineage_across_restarts(tmp_path):
    session=SessionEngine(tmp_path,"compact",budget=Budget(tool_calls=3)); session.begin_incremental("preserve constraints")
    ids=[]
    for index in range(3):
        session.checkpoint([{"role":"user","content":"constraint λ","_smara_mandatory":True}],{"phase":"model","iteration":index})
        ident=session.get("continuation_artifact_id"); ids.append(ident)
        payload=json.loads(session.resolve_artifact(ident))
        assert payload["objective"]=="preserve constraints"
        if index: assert payload["parent_checkpoint_id"]==ids[index-1]
        session.close(); session=SessionEngine(tmp_path,"compact",budget=Budget(tool_calls=3))
    assert len(set(ids))==3


def test_corrupt_checkpoint_artifact_is_rejected(tmp_path):
    session=SessionEngine(tmp_path,"corrupt"); session.begin_incremental("x"); session.checkpoint([{"role":"user","content":"x"}],{"phase":"model"})
    ident=session.get("continuation_artifact_id"); path=next(session.artifacts.glob(f"{ident}.*")); path.write_bytes(b"corrupt")
    with pytest.raises(ValueError,match="hash mismatch"): session.resolve_artifact(ident)


def test_h3_fixture_manifest_definitions_are_tamper_evident():
    manifest_path=Path(__file__).parent/"evals"/"local_execution"/"manifest.json"
    manifest=json.loads(manifest_path.read_text(encoding="utf-8"))
    assert [case["id"] for case in manifest["cases"]]==[f"R{i}" for i in range(25,33)]
    for case in manifest["cases"]:
        definition="|".join((case["id"],case["fixture"],case["validator"])).encode()
        assert hashlib.sha256(definition).hexdigest()==case["definition_sha256"]
