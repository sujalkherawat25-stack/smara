import pytest
from pathlib import Path
import sys

sys.path.insert(0, "src")
from smara.dag_flow import DAGNode, DAGWorkflow, NodeStatus, CycleDetectedError


def test_dag_workflow_execution():
    wf = DAGWorkflow(title="Test Pipeline")

    # Node A: fetch data
    node_a = DAGNode(id="fetch", title="Fetch Data", capability="web_extract", payload={"url": "test"})
    # Node B: parse data (depends on A)
    node_b = DAGNode(id="parse", title="Parse Data", capability="python_execute", depends_on=["fetch"])
    # Node C: summarize (depends on B)
    node_c = DAGNode(id="summarize", title="Summarize", capability="reasoning", depends_on=["parse"])

    wf.add_node(node_a)
    wf.add_node(node_b)
    wf.add_node(node_c)

    # Topological order test
    order = wf.validate()
    assert order == ["fetch", "parse", "summarize"]

    # Initial readiness test
    ready = wf.get_ready_nodes()
    assert len(ready) == 1
    assert ready[0].id == "fetch"

    # Execution callback simulator
    def executor(node: DAGNode):
        return f"result_of_{node.id}"

    # Step 1
    executed = wf.step(executor)
    assert len(executed) == 1
    assert executed[0].id == "fetch"
    assert executed[0].status == NodeStatus.COMPLETED

    # Step 2
    executed = wf.step(executor)
    assert len(executed) == 1
    assert executed[0].id == "parse"
    assert executed[0].status == NodeStatus.COMPLETED

    # Step 3
    executed = wf.step(executor)
    assert len(executed) == 1
    assert executed[0].id == "summarize"
    assert executed[0].status == NodeStatus.COMPLETED

    # Final completion check
    summary = wf.run_until_complete(executor)
    assert summary["is_complete"] is True
    assert summary["has_failures"] is False


def test_cycle_detection():
    wf = DAGWorkflow(title="Cyclic Graph")
    n1 = DAGNode(id="n1", title="N1", capability="c", depends_on=["n2"])
    n2 = DAGNode(id="n2", title="N2", capability="c", depends_on=["n1"])
    wf.add_node(n1)
    wf.add_node(n2)

    try:
        wf.validate()
        assert False, "Should have raised CycleDetectedError"
    except CycleDetectedError:
        pass


def test_dynamic_node_injection():
    wf = DAGWorkflow(title="Dynamic Graph")
    n1 = DAGNode(id="step1", title="Step 1", capability="c")
    n2 = DAGNode(id="step2", title="Step 2", capability="c", depends_on=["step1"])
    wf.add_node(n1)
    wf.add_node(n2)

    # Inject healing step between step1 and step2
    heal_node = DAGNode(id="heal", title="Heal Step", capability="heal")
    wf.inject_node(heal_node, after_node_id="step1", before_node_id="step2")

    order = wf.validate()
    assert order == ["step1", "heal", "step2"]


if __name__ == "__main__":
    test_dag_workflow_execution()
    test_cycle_detection()
    test_dynamic_node_injection()
    print("All dag_flow tests passed successfully!")
