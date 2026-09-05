import json
import tempfile
from pathlib import Path
import sys

sys.path.insert(0, "src")
from smara.agent_tools import (
    memory_tool,
    skills_list_tool,
    skill_view_tool,
    delegate_task_tool,
    dag_flow_tool
)
from smara.task_memory import TaskMemoryStore, get_default_memory_store
from smara.skills_system import SkillsRegistry, get_default_skills_registry
from smara.dag_flow import DAGNode, DAGWorkflow


def test_integrated_tools(tmp_path: Path, monkeypatch):
    # 1. Test Memory Tool Actions
    store = TaskMemoryStore(tmp_path / "memory")
    monkeypatch.setattr("smara.task_memory._default_store", store)
    add_res = json.loads(memory_tool("add", target="memory", content="Testing integrated architecture."))
    assert add_res["status"] in ("success", "noop")

    search_res = json.loads(memory_tool("search", query="integrated architecture"))
    assert len(search_res) >= 1

    rep_res = json.loads(memory_tool("replace", target="memory", old_text="integrated architecture", content="Testing modernized Smara agent."))
    assert rep_res["status"] == "success"

    # 2. Test Skills Tool Actions
    with tempfile.TemporaryDirectory() as tmpdir:
        ws = Path(tmpdir)
        skill_dir = ws / ".smara" / "skills" / "demo-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("---\nname: demo-skill\ndescription: A demo skill\n---\n# Demo Instructions", encoding="utf-8")

        reg = get_default_skills_registry(workspace_dir=ws)
        skills = json.loads(skills_list_tool())
        assert any(s["name"] == "demo-skill" for s in skills)

        view = json.loads(skill_view_tool("demo-skill"))
        assert view["status"] == "success"
        assert "Demo Instructions" in view["instructions"]

    # 3. Test DAG Flow Tool
    wf = DAGWorkflow(title="Test Tool DAG")
    wf.add_node(DAGNode(id="n1", title="Init", capability="init"))
    wf.add_node(DAGNode(id="n2", title="Process", capability="proc", depends_on=["n1"]))

    dag_res = json.loads(dag_flow_tool("create_and_run", workflow_data=json.dumps(wf.to_dict())))
    assert dag_res["is_complete"] is True
    assert dag_res["has_failures"] is False

    print("All integrated capability tests passed successfully!")


if __name__ == "__main__":
    test_integrated_tools()
