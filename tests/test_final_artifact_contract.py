from smara.harness import SessionEngine,ToolCall


def test_finalization_rechecks_expected_content_after_user_edit(tmp_path):
    engine=SessionEngine(tmp_path,"artifact")
    engine.begin_incremental("produce answer.json containing answer 42")
    engine.set("output_contract",{"artifacts":[{"path":"answer.json","kind":"json","checks":{"expected":{"answer":42}}}]})
    path=tmp_path/"answer.json"
    path.write_text('{"answer":42}')
    assert engine.finish_incremental("completed","done")["status"]=="completed"
    path.write_text('{"answer":99}')
    result=engine.finish_incremental("completed","done")
    assert result["status"]=="needs_input"
    assert any("wrong_values" in error for error in result["unresolved_items"])
    engine.close()


def test_empty_expected_csv_rejects_extra_rows(tmp_path):
    engine=SessionEngine(tmp_path,"csv")
    engine.begin_incremental("empty CSV")
    engine.set("output_contract",{"artifacts":[{"path":"answer.csv","kind":"csv","checks":{"expected_rows":[]}}]})
    (tmp_path/"answer.csv").write_text("value\nwrong\n")
    assert engine.finish_incremental("completed","done")["status"]=="needs_input"
    engine.close()


def test_passing_declared_artifact_verifies_the_mutated_revision(tmp_path):
    engine=SessionEngine(tmp_path,"validated-mutation")
    engine.begin_incremental("produce answer.json")
    engine.set("output_contract",{"artifacts":[{"path":"answer.json","kind":"json","checks":{"expected":{"value":42}}}]})
    call=ToolCall("write","write_file",{"path":"answer.json","content":'{"value":42}'},str(tmp_path))
    result=engine.execute_incremental(call,lambda _raw:engine.broker.dispatch(call))
    assert result.ok
    completed=engine.finish_incremental("completed","done")
    assert completed["status"]=="completed"
    assert not completed["unresolved_items"]
    engine.close()
