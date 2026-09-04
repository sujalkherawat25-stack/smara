import { useEffect, useState } from "react";
import { desktop, isNativeDesktop } from "../api";
import type { DAGNodeData, DAGWorkflowData } from "../types";

export function DAGFlowTab({ onSetNotice }: { onSetNotice: (msg: string) => void }) {
  const [workflow, setWorkflow] = useState<DAGWorkflowData | null>(null);
  const [loading, setLoading] = useState(false);
  const [stepping, setStepping] = useState(false);
  const [showAscii, setShowAscii] = useState(false);
  const [showInjectModal, setShowInjectModal] = useState(false);
  const [injectTitle, setInjectTitle] = useState("");
  const [injectCapability, setInjectCapability] = useState("local_terminal");
  const [injectAfter, setInjectAfter] = useState<string>("");
  const [injectBefore, setInjectBefore] = useState<string>("");

  const refreshWorkflow = async () => {
    setLoading(true);
    try {
      if (isNativeDesktop) {
        const wf = await desktop.getDagWorkflow();
        setWorkflow(wf);
      } else {
        const mockWf: DAGWorkflowData = {
          id: "smara_verification_flow",
          title: "Smara Autonomous Verification Pipeline",
          is_paused: false,
          nodes: [
            {
              id: "inspect_env",
              title: "Inspect Environment & Working Tree",
              capability: "local_terminal",
              payload: { command: "git status" },
              depends_on: [],
              status: "READY",
              duration_ms: 0,
              retries: 0,
              max_retries: 2,
            },
            {
              id: "run_tests",
              title: "Pytest Verification Suite",
              capability: "test_suite",
              payload: {},
              depends_on: ["inspect_env"],
              status: "PENDING",
              duration_ms: 0,
              retries: 0,
              max_retries: 2,
            },
            {
              id: "ast_analysis",
              title: "AST Blast Radius & Symbol Check",
              capability: "ast_graph",
              payload: {},
              depends_on: ["inspect_env"],
              status: "PENDING",
              duration_ms: 0,
              retries: 0,
              max_retries: 2,
            },
            {
              id: "security_audit",
              title: "Sanitize Injections & Coding Conventions",
              capability: "security_audit",
              payload: {},
              depends_on: ["run_tests", "ast_analysis"],
              status: "PENDING",
              duration_ms: 0,
              retries: 0,
              max_retries: 2,
            },
            {
              id: "synthesis_report",
              title: "Synthesize Deployment Scorecard",
              capability: "report",
              payload: {},
              depends_on: ["security_audit"],
              status: "PENDING",
              duration_ms: 0,
              retries: 0,
              max_retries: 2,
            },
          ],
        };
        setWorkflow(mockWf);
      }
    } catch (err: any) {
      onSetNotice(`Error loading DAG: ${err?.message || String(err)}`);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void refreshWorkflow();
  }, []);

  const handleStep = async () => {
    if (!workflow || stepping) return;
    setStepping(true);
    try {
      if (isNativeDesktop) {
        const next = await desktop.stepDagWorkflow(workflow);
        setWorkflow(next);
        onSetNotice("✓ Executed READY DAG nodes in current topological wave.");
      } else {
        // Mock step: find ready nodes, mark completed, update dependencies
        const updated = { ...workflow };
        const ready = updated.nodes.filter((n) => n.status === "READY");
        ready.forEach((n) => {
          n.status = "COMPLETED";
          n.duration_ms = 45;
          n.result = `Successfully executed: ${n.title}`;
        });
        // check next ready
        updated.nodes.forEach((n) => {
          if (n.status === "PENDING") {
            const deps = updated.nodes.filter((d) => n.depends_on.includes(d.id));
            if (deps.length > 0 && deps.every((d) => d.status === "COMPLETED")) {
              n.status = "READY";
            }
          }
        });
        setWorkflow(updated);
        onSetNotice("✓ Stepped mock DAG flow.");
      }
    } catch (err: any) {
      onSetNotice(`Step error: ${err?.message || String(err)}`);
    } finally {
      setStepping(false);
    }
  };

  const handleRunAll = async () => {
    if (!workflow || stepping) return;
    setStepping(true);
    try {
      if (isNativeDesktop) {
        const next = await desktop.runDagWorkflow(workflow);
        setWorkflow(next);
        onSetNotice("✓ Ran full DAG workflow to completion.");
      } else {
        const updated = { ...workflow };
        updated.nodes.forEach((n) => {
          n.status = "COMPLETED";
          n.duration_ms = 50;
          n.result = `Executed: ${n.title}`;
        });
        setWorkflow(updated);
        onSetNotice("✓ Ran mock DAG to completion.");
      }
    } catch (err: any) {
      onSetNotice(`Run error: ${err?.message || String(err)}`);
    } finally {
      setStepping(false);
    }
  };

  const handleRetryNode = async (nodeId: string) => {
    if (!workflow) return;
    try {
      if (isNativeDesktop) {
        const next = await desktop.retryDagNode(workflow, nodeId);
        setWorkflow(next);
        onSetNotice(`✓ Reset node '${nodeId}' and downstream dependents.`);
      } else {
        const updated = { ...workflow };
        const target = updated.nodes.find((n) => n.id === nodeId);
        if (target) {
          target.status = "READY";
          target.result = null;
        }
        setWorkflow(updated);
        onSetNotice(`✓ Reset node '${nodeId}'.`);
      }
    } catch (err: any) {
      onSetNotice(`Retry error: ${err?.message || String(err)}`);
    }
  };

  const handleInjectNode = async () => {
    if (!workflow || !injectTitle.trim()) return;
    const cleanId = `inject_${Date.now() % 10000}`;
    const newNode: Partial<DAGNodeData> = {
      id: cleanId,
      title: injectTitle.trim(),
      capability: injectCapability,
      payload: {},
      depends_on: injectAfter ? [injectAfter] : [],
      status: "PENDING",
      retries: 0,
      max_retries: 2,
    };

    try {
      if (isNativeDesktop) {
        const next = await desktop.injectDagNode(
          workflow,
          newNode,
          injectAfter || undefined,
          injectBefore || undefined
        );
        setWorkflow(next);
        onSetNotice(`✓ Injected dynamic node '${injectTitle.trim()}' into DAG.`);
      } else {
        const updated = { ...workflow };
        updated.nodes.push(newNode as DAGNodeData);
        setWorkflow(updated);
        onSetNotice(`✓ Injected mock node '${injectTitle.trim()}'.`);
      }
      setShowInjectModal(false);
      setInjectTitle("");
      setInjectAfter("");
      setInjectBefore("");
    } catch (err: any) {
      onSetNotice(`Inject error: ${err?.message || String(err)}`);
    }
  };

  const nodes = workflow?.nodes || [];
  const statusCounts = {
    READY: nodes.filter((n) => n.status === "READY").length,
    RUNNING: nodes.filter((n) => n.status === "RUNNING").length,
    COMPLETED: nodes.filter((n) => n.status === "COMPLETED").length,
    FAILED: nodes.filter((n) => n.status === "FAILED").length,
    BLOCKED: nodes.filter((n) => n.status === "BLOCKED").length,
    PENDING: nodes.filter((n) => n.status === "PENDING").length,
  };

  const getNodeColor = (status: string) => {
    switch (status) {
      case "READY":
        return { border: "#38bdf8", bg: "rgba(56, 189, 248, 0.1)", text: "#38bdf8" };
      case "RUNNING":
        return { border: "#a855f7", bg: "rgba(168, 85, 247, 0.1)", text: "#c084fc" };
      case "COMPLETED":
        return { border: "#34d399", bg: "rgba(52, 211, 153, 0.1)", text: "#34d399" };
      case "FAILED":
        return { border: "#f87171", bg: "rgba(248, 113, 113, 0.1)", text: "#f87171" };
      case "BLOCKED":
        return { border: "#fbbf24", bg: "rgba(251, 191, 36, 0.1)", text: "#fbbf24" };
      default:
        return { border: "#30363d", bg: "#161b22", text: "#8b949e" };
    }
  };

  return (
    <div className="tab-pane-container">
      {/* Header */}
      <div className="pane-header">
        <div>
          <h2>⚡ Interactive DAG Flow Visualizer & Orchestrator</h2>
          <p>
            Directed Acyclic Graph execution engine with Kahn's topological sorting, cycle detection, single-step execution, and dynamic node injection.
          </p>
        </div>
        <div style={{ display: "flex", gap: "8px" }}>
          <button
            type="button"
            className="btn-create-branch-confirm"
            onClick={() => void handleStep()}
            disabled={stepping || statusCounts.READY === 0}
          >
            {stepping ? "Stepping..." : "▶️ Step Ready"}
          </button>
          <button
            type="button"
            className="btn-inspect-symbol"
            onClick={() => void handleRunAll()}
            disabled={stepping || (statusCounts.READY === 0 && statusCounts.PENDING === 0)}
          >
            ⏩ Run All
          </button>
          <button type="button" className="btn-new-branch" onClick={() => setShowInjectModal(true)}>
            + Inject Dynamic Node
          </button>
          <button
            type="button"
            className="btn-refresh-git"
            onClick={() => setShowAscii(!showAscii)}
          >
            {showAscii ? "Canvas View" : "ASCII View"}
          </button>
          <button type="button" className="btn-refresh-git" onClick={() => void refreshWorkflow()} disabled={loading}>
            🔄 Reset
          </button>
        </div>
      </div>

      {/* Status Counters Bar */}
      <div style={{ display: "flex", gap: "12px", marginBottom: "16px", flexWrap: "wrap" }}>
        <div style={{ background: "#111827", border: "1px solid #30363d", borderRadius: "6px", padding: "8px 14px", display: "flex", alignItems: "center", gap: "8px" }}>
          <span style={{ fontSize: "11px", color: "#8b949e" }}>Workflow:</span>
          <strong style={{ fontSize: "12.5px", color: "#fff" }}>{workflow?.title || "Smara DAG"}</strong>
        </div>
        <div style={{ background: "rgba(56, 189, 248, 0.1)", border: "1px solid rgba(56, 189, 248, 0.3)", borderRadius: "6px", padding: "8px 12px" }}>
          <span style={{ fontSize: "12px", color: "#38bdf8", fontWeight: 700 }}>READY: {statusCounts.READY}</span>
        </div>
        <div style={{ background: "rgba(168, 85, 247, 0.1)", border: "1px solid rgba(168, 85, 247, 0.3)", borderRadius: "6px", padding: "8px 12px" }}>
          <span style={{ fontSize: "12px", color: "#c084fc", fontWeight: 700 }}>RUNNING: {statusCounts.RUNNING}</span>
        </div>
        <div style={{ background: "rgba(52, 211, 153, 0.1)", border: "1px solid rgba(52, 211, 153, 0.3)", borderRadius: "6px", padding: "8px 12px" }}>
          <span style={{ fontSize: "12px", color: "#34d399", fontWeight: 700 }}>COMPLETED: {statusCounts.COMPLETED}</span>
        </div>
        {statusCounts.FAILED > 0 && (
          <div style={{ background: "rgba(248, 113, 113, 0.1)", border: "1px solid rgba(248, 113, 113, 0.3)", borderRadius: "6px", padding: "8px 12px" }}>
            <span style={{ fontSize: "12px", color: "#f87171", fontWeight: 700 }}>FAILED: {statusCounts.FAILED}</span>
          </div>
        )}
        {statusCounts.BLOCKED > 0 && (
          <div style={{ background: "rgba(251, 191, 36, 0.1)", border: "1px solid rgba(251, 191, 36, 0.3)", borderRadius: "6px", padding: "8px 12px" }}>
            <span style={{ fontSize: "12px", color: "#fbbf24", fontWeight: 700 }}>BLOCKED: {statusCounts.BLOCKED}</span>
          </div>
        )}
        <div style={{ background: "#161b22", border: "1px solid #30363d", borderRadius: "6px", padding: "8px 12px" }}>
          <span style={{ fontSize: "12px", color: "#8b949e" }}>PENDING: {statusCounts.PENDING}</span>
        </div>
      </div>

      {/* ASCII View Toggle */}
      {showAscii && (
        <div style={{ background: "#090d13", border: "1px solid #30363d", borderRadius: "8px", padding: "16px", marginBottom: "16px" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" }}>
            <span style={{ fontSize: "12px", color: "#38bdf8", fontWeight: 700 }}>Text Topological Graph Representation:</span>
            <button
              type="button"
              onClick={() => setShowAscii(false)}
              style={{ background: "transparent", border: "none", color: "#8b949e", cursor: "pointer" }}
            >
              ✕ Close
            </button>
          </div>
          <pre style={{ margin: 0, fontSize: "12px", color: "#e6edf3", fontFamily: "Consolas, monospace", lineHeight: 1.6 }}>
            {workflow?.ascii_view ||
              `=== Workflow: ${workflow?.title} ===\n` +
                nodes
                  .map(
                    (n) =>
                      `  [${n.status.slice(0, 4)}] ${n.id}: ${n.title} [${n.capability}]${
                        n.depends_on.length ? ` <- (${n.depends_on.join(", ")})` : ""
                      }`
                  )
                  .join("\n")}
          </pre>
        </div>
      )}

      {/* Visual DAG Nodes Pipeline */}
      <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
        {nodes.map((node, i) => {
          const colors = getNodeColor(node.status);
          return (
            <div
              key={node.id}
              style={{
                background: colors.bg,
                border: `1px solid ${colors.border}`,
                borderRadius: "8px",
                padding: "14px 18px",
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                gap: "16px",
              }}
            >
              <div style={{ display: "flex", flexDirection: "column", gap: "6px", flex: 1 }}>
                <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                  <span
                    style={{
                      fontSize: "11px",
                      fontWeight: 800,
                      textTransform: "uppercase",
                      color: colors.text,
                      background: "rgba(0, 0, 0, 0.4)",
                      padding: "2px 8px",
                      borderRadius: "4px",
                      border: `1px solid ${colors.border}`,
                    }}
                  >
                    {node.status}
                  </span>
                  <strong style={{ fontSize: "14px", color: "#fff" }}>{node.title}</strong>
                  <code style={{ fontSize: "11px", color: "#8b949e" }}>({node.id})</code>
                  <span
                    style={{
                      fontSize: "10px",
                      color: "#94a3b8",
                      background: "#21262d",
                      padding: "1px 6px",
                      borderRadius: "3px",
                    }}
                  >
                    {node.capability}
                  </span>
                </div>

                {node.depends_on.length > 0 && (
                  <div style={{ display: "flex", alignItems: "center", gap: "6px", fontSize: "11.5px", color: "#8b949e" }}>
                    <span>Depends on:</span>
                    {node.depends_on.map((dep) => (
                      <span
                        key={dep}
                        style={{
                          background: "#090d13",
                          border: "1px solid #30363d",
                          borderRadius: "4px",
                          padding: "1px 6px",
                          color: "#cbd5e1",
                          fontFamily: "monospace",
                        }}
                      >
                        {dep}
                      </span>
                    ))}
                  </div>
                )}

                {node.result && (
                  <div style={{ fontSize: "12px", color: "#34d399", fontFamily: "monospace", marginTop: "2px" }}>
                    ✓ {node.result}
                  </div>
                )}
                {node.error && (
                  <div style={{ fontSize: "12px", color: "#f87171", fontFamily: "monospace", marginTop: "2px" }}>
                    ⚠️ {node.error}
                  </div>
                )}
              </div>

              <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                {node.duration_ms > 0 && (
                  <span style={{ fontSize: "11.5px", color: "#8b949e" }}>{node.duration_ms}ms</span>
                )}
                {(node.status === "COMPLETED" || node.status === "FAILED" || node.status === "BLOCKED") && (
                  <button
                    type="button"
                    onClick={() => void handleRetryNode(node.id)}
                    style={{
                      background: "transparent",
                      border: "1px solid #30363d",
                      color: "#38bdf8",
                      padding: "4px 10px",
                      borderRadius: "6px",
                      fontSize: "12px",
                      cursor: "pointer",
                    }}
                  >
                    Retry Node
                  </button>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {/* Dynamic Node Injection Modal */}
      {showInjectModal && (
        <div className="modal-backdrop" onClick={() => setShowInjectModal(false)}>
          <div className="modal-dialog-card" onClick={(e) => e.stopPropagation()} style={{ maxWidth: "520px" }}>
            <h3>Inject Dynamic DAG Node</h3>
            <p>
              Dynamically insert a diagnostic, repair, or validation step into the execution flow. Kahn's cycle detection validates topology.
            </p>
            <div style={{ display: "flex", flexDirection: "column", gap: "10px", margin: "10px 0" }}>
              <input
                type="text"
                value={injectTitle}
                onChange={(e) => setInjectTitle(e.target.value)}
                placeholder="Node Title (e.g., AST Mutation Rollback Check)"
              />
              <select
                value={injectCapability}
                onChange={(e) => setInjectCapability(e.target.value)}
                style={{
                  background: "#090d13",
                  border: "1px solid #30363d",
                  borderRadius: "8px",
                  padding: "8px 12px",
                  color: "#fff",
                  fontSize: "13px",
                }}
              >
                <option value="local_terminal">local_terminal (Shell command)</option>
                <option value="ast_graph">ast_graph (Code Property Graph)</option>
                <option value="test_suite">test_suite (Pytest verification)</option>
                <option value="security_audit">security_audit (Sanitizer & audit)</option>
                <option value="report">report (Scorecard synthesis)</option>
              </select>

              <div style={{ display: "flex", gap: "8px" }}>
                <div style={{ flex: 1 }}>
                  <span style={{ fontSize: "11px", color: "#8b949e" }}>Execute After:</span>
                  <select
                    value={injectAfter}
                    onChange={(e) => setInjectAfter(e.target.value)}
                    style={{
                      width: "100%",
                      background: "#090d13",
                      border: "1px solid #30363d",
                      borderRadius: "8px",
                      padding: "8px",
                      color: "#fff",
                      fontSize: "12px",
                    }}
                  >
                    <option value="">None (Root Node)</option>
                    {nodes.map((n) => (
                      <option key={n.id} value={n.id}>
                        {n.title} ({n.id})
                      </option>
                    ))}
                  </select>
                </div>

                <div style={{ flex: 1 }}>
                  <span style={{ fontSize: "11px", color: "#8b949e" }}>Execute Before:</span>
                  <select
                    value={injectBefore}
                    onChange={(e) => setInjectBefore(e.target.value)}
                    style={{
                      width: "100%",
                      background: "#090d13",
                      border: "1px solid #30363d",
                      borderRadius: "8px",
                      padding: "8px",
                      color: "#fff",
                      fontSize: "12px",
                    }}
                  >
                    <option value="">None (Leaf Node)</option>
                    {nodes.map((n) => (
                      <option key={n.id} value={n.id}>
                        {n.title} ({n.id})
                      </option>
                    ))}
                  </select>
                </div>
              </div>
            </div>

            <div className="modal-actions">
              <button type="button" onClick={() => setShowInjectModal(false)}>Cancel</button>
              <button
                type="button"
                className="btn-create-branch-confirm"
                onClick={() => void handleInjectNode()}
                disabled={!injectTitle.trim()}
              >
                Inject Node
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
