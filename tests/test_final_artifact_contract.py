from smara.harness import SessionEngine


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
