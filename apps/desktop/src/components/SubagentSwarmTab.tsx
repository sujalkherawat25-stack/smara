import { useEffect, useState } from "react";
import { desktop, isNativeDesktop } from "../api";
import type { SubagentDelegationData, SubagentRolesData, SwarmTaskResultData } from "../types";

export function SubagentSwarmTab({ onSetNotice }: { onSetNotice: (msg: string) => void }) {
  const [rolesData, setRolesData] = useState<SubagentRolesData | null>(null);
  const [selectedRole, setSelectedRole] = useState("researcher");
  const [goal, setGoal] = useState("");
  const [context, setContext] = useState("");
  const [delegating, setDelegating] = useState(false);
  const [delegationHistory, setDelegationHistory] = useState<SubagentDelegationData[]>([]);

  // Swarm Full Pipeline States
  const [swarmObjective, setSwarmObjective] = useState("");
  const [runningSwarm, setRunningSwarm] = useState(false);
  const [swarmResult, setSwarmResult] = useState<SwarmTaskResultData | null>(null);
  const [agentStates, setAgentStates] = useState<Record<string, "idle" | "working" | "completed" | "failed">>({
    architect: "idle",
    implementer: "idle",
    verifier: "idle",
    auditor: "idle",
  });

  const refreshRoles = async () => {
    try {
      if (isNativeDesktop) {
        const data = await desktop.getSubagentRoles();
        setRolesData(data);
      } else {
        setRolesData({
          roles: [
            { id: "researcher", name: "Documentation & Web Researcher", description: "Researches reference docs, parses complex PDFs, and crawls web sources." },
            { id: "coder", name: "Codebase & AST Engineer", description: "Inspects AST property graphs, checks symbol references, and edits source code in isolation." },
            { id: "tester", name: "Test Runner & Verifier", description: "Runs automated pytest suites, analyzes blast radius, and checks test passes." },
            { id: "auditor", name: "Security & Convention Auditor", description: "Audits coding style invariants, detects prompt injections, and validates clean architecture." },
            { id: "generalist", name: "Autonomous Generalist", description: "General task executor with bounded steps and safety controls." },
          ],
          blocked_tools: ["delegate_task", "memory", "clarify", "dag_flow"],
          safe_tools: ["file_read", "file_write", "terminal_command", "browser_scrape", "ast_search", "web_search", "pytest_runner"],
        });
      }
    } catch {
      // ignore
    }
  };

  useEffect(() => {
    void refreshRoles();
  }, []);

  const handleDelegate = async () => {
    if (!goal.trim() || delegating) return;
    setDelegating(true);
    try {
      if (isNativeDesktop) {
        const res = await desktop.runSubagentDelegation(goal.trim(), selectedRole, context.trim() || undefined);
        setDelegationHistory((prev) => [res, ...prev]);
        onSetNotice(`✓ Delegated task '${res.task_id}' completed with status ${res.status}`);
      } else {
        const mockRes: SubagentDelegationData = {
          task_id: `sub_${selectedRole}_${Date.now() % 10000}`,
          goal: goal.trim(),
          status: "SUCCESS",
          summary: `Successfully completed research and execution for: ${goal.trim()}. Verified AST invariants and clean isolation.`,
          trace_steps: 3,
          duration_ms: 1150,
          tools_used: ["ast_search", "file_read", "pytest_runner"],
        };
        setDelegationHistory((prev) => [mockRes, ...prev]);
        onSetNotice(`✓ Delegated mock task '${mockRes.task_id}' completed.`);
      }
      setGoal("");
      setContext("");
    } catch (err: any) {
      onSetNotice(`Delegation error: ${err?.message || String(err)}`);
    } finally {
      setDelegating(false);
    }
  };

  const handleLaunchSwarm = async () => {
    if (!swarmObjective.trim() || runningSwarm) return;
    setRunningSwarm(true);
    setAgentStates({
      architect: "working",
      implementer: "idle",
      verifier: "idle",
      auditor: "idle",
    });

    try {
      if (isNativeDesktop) {
        const res = await desktop.runSwarmTask(swarmObjective.trim());
        setSwarmResult(res);
        setAgentStates({
          architect: "completed",
          implementer: "completed",
          verifier: "completed",
          auditor: "completed",
        });
        onSetNotice(`✓ Swarm completed: ${res.status} in ${res.duration_ms}ms`);
      } else {
        setTimeout(() => setAgentStates((prev) => ({ ...prev, architect: "completed", implementer: "working" })), 600);
        setTimeout(() => setAgentStates((prev) => ({ ...prev, implementer: "completed", verifier: "working" })), 1200);
        setTimeout(() => {
          setAgentStates({
            architect: "completed",
            implementer: "completed",
            verifier: "completed",
            auditor: "completed",
          });
          setSwarmResult({
            session_id: "swarm-session-1",
            objective: swarmObjective.trim(),
            status: "SUCCESS",
            duration_ms: 1850,
            architect_plan: {
              objective: swarmObjective.trim(),
              target_symbols: ["SmaraAutonomousAgent", "SubagentOrchestrator"],
              blast_radius: ["TaskMemoryStore"],
              adrs_consulted: ["Dual-Plane Memory Architecture"],
              conventions_noted: ["Strict type annotation coverage (100%)"],
              steps: ["1. Task decomposition", "2. AST mutation with pre-flight snapshot", "3. Test verification", "4. Auditor review"],
              risk_level: "LOW",
            },
            files_modified: ["src/smara/subagent_orchestrator.py"],
            tests_run: 5,
            tests_passed: 5,
            healing_applied: false,
            audit_passed: true,
            commit_message: `feat(swarm): ${swarmObjective.trim().toLowerCase()}`,
            inter_agent_messages: [],
          });
          onSetNotice("✓ Swarm completed: SUCCESS");
        }, 1800);
      }
    } catch (err: any) {
      onSetNotice(`Swarm error: ${err?.message || String(err)}`);
    } finally {
      setRunningSwarm(false);
    }
  };

  return (
    <div className="tab-pane-container swarm-container">
      {/* Header */}
      <div className="pane-header">
        <div>
          <h2>🐝 Autonomous Swarm Teamwork & Subagent Orchestration</h2>
          <p>
            Isolated child contexts, strict tool safety gating, parallel thread pooling, and token-saving parent synthesis.
          </p>
        </div>
      </div>

      {/* Safety Architecture Overview Cards */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: "12px", marginBottom: "16px" }}>
        <div style={{ background: "rgba(16, 185, 129, 0.08)", border: "1px solid rgba(16, 185, 129, 0.25)", borderRadius: "8px", padding: "12px" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "6px", marginBottom: "4px" }}>
            <span style={{ fontSize: "14px" }}>🛡️</span>
            <strong style={{ fontSize: "13px", color: "#34d399" }}>Tool Safety Gating</strong>
          </div>
          <p style={{ margin: 0, fontSize: "11.5px", color: "#94a3b8" }}>
            Children stripped of <code>delegate_task</code>, <code>memory</code>, <code>dag_flow</code> to eliminate infinite recursion and injection.
          </p>
        </div>

        <div style={{ background: "rgba(56, 189, 248, 0.08)", border: "1px solid rgba(56, 189, 248, 0.25)", borderRadius: "8px", padding: "12px" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "6px", marginBottom: "4px" }}>
            <span style={{ fontSize: "14px" }}>🧠</span>
            <strong style={{ fontSize: "13px", color: "#38bdf8" }}>Isolated Conversation Scopes</strong>
          </div>
          <p style={{ margin: 0, fontSize: "11.5px", color: "#94a3b8" }}>
            Each worker runs with its own scoped prompt and independent message history, eliminating token bloat.
          </p>
        </div>

        <div style={{ background: "rgba(168, 85, 247, 0.08)", border: "1px solid rgba(168, 85, 247, 0.25)", borderRadius: "8px", padding: "12px" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "6px", marginBottom: "4px" }}>
            <span style={{ fontSize: "14px" }}>⚡</span>
            <strong style={{ fontSize: "13px", color: "#c084fc" }}>Parent Synthesis</strong>
          </div>
          <p style={{ margin: 0, fontSize: "11.5px", color: "#94a3b8" }}>
            Parent receives distilled verified summaries instead of verbose multi-thousand token raw subagent turn logs.
          </p>
        </div>
      </div>

      {/* Subagent Worker Delegation Section */}
      <div style={{ background: "#111827", border: "1px solid #30363d", borderRadius: "8px", padding: "16px", marginBottom: "20px" }}>
        <h3 style={{ margin: "0 0 12px 0", fontSize: "15px", color: "#fff" }}>
          🚀 Delegate Scoped Task to Specialized Subagent Worker
        </h3>

        {/* Worker Roles Picker */}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: "8px", marginBottom: "14px" }}>
          {(rolesData?.roles || []).map((r) => {
            const isSelected = selectedRole === r.id;
            return (
              <div
                key={r.id}
                onClick={() => setSelectedRole(r.id)}
                style={{
                  background: isSelected ? "rgba(56, 189, 248, 0.15)" : "#161b22",
                  border: isSelected ? "1px solid #38bdf8" : "1px solid #30363d",
                  borderRadius: "6px",
                  padding: "8px 10px",
                  cursor: "pointer",
                }}
              >
                <strong style={{ fontSize: "12px", color: isSelected ? "#38bdf8" : "#fff" }}>{r.name}</strong>
                <p style={{ margin: "3px 0 0 0", fontSize: "10.5px", color: "#8b949e", lineHeight: 1.3 }}>
                  {r.description}
                </p>
              </div>
            );
          })}
        </div>

        {/* Delegation Inputs */}
        <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
          <input
            type="text"
            value={goal}
            onChange={(e) => setGoal(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && void handleDelegate()}
            placeholder="Delegated Task Goal (e.g., Audit AST blast radius of coding_memory.py)..."
            style={{
              background: "#090d13",
              border: "1px solid #30363d",
              borderRadius: "6px",
              padding: "10px 12px",
              color: "#fff",
              fontSize: "13px",
            }}
          />
          <input
            type="text"
            value={context}
            onChange={(e) => setContext(e.target.value)}
            placeholder="Optional relevant context snippet or target file paths..."
            style={{
              background: "#090d13",
              border: "1px solid #30363d",
              borderRadius: "6px",
              padding: "8px 12px",
              color: "#fff",
              fontSize: "12px",
            }}
          />
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <div style={{ display: "flex", gap: "6px", alignItems: "center" }}>
              <span style={{ fontSize: "11px", color: "#8b949e" }}>Blocked Dangerous Tools:</span>
              {(rolesData?.blocked_tools || ["delegate_task", "memory", "clarify", "dag_flow"]).map((bt) => (
                <span key={bt} style={{ fontSize: "10px", color: "#f87171", background: "rgba(248, 113, 113, 0.15)", padding: "1px 5px", borderRadius: "3px" }}>
                  🚫 {bt}
                </span>
              ))}
            </div>
            <button
              type="button"
              className="btn-create-branch-confirm"
              onClick={() => void handleDelegate()}
              disabled={delegating || !goal.trim()}
            >
              {delegating ? "Worker Running..." : "Delegate to Subagent"}
            </button>
          </div>
        </div>
      </div>

      {/* Delegation Activity Stream */}
      {delegationHistory.length > 0 && (
        <div style={{ marginBottom: "24px" }}>
          <h4 style={{ margin: "0 0 10px 0", fontSize: "13px", color: "#38bdf8", textTransform: "uppercase" }}>
            📋 Recent Subagent Delegations ({delegationHistory.length})
          </h4>
          <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
            {delegationHistory.map((d, i) => (
              <div
                key={i}
                style={{
                  background: "#161b22",
                  border: "1px solid #30363d",
                  borderRadius: "8px",
                  padding: "14px",
                  display: "flex",
                  flexDirection: "column",
                  gap: "6px",
                }}
              >
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                    <span
                      style={{
                        fontSize: "10.5px",
                        fontWeight: 700,
                        color: d.status === "SUCCESS" ? "#34d399" : "#f87171",
                        background: d.status === "SUCCESS" ? "rgba(52, 211, 153, 0.15)" : "rgba(248, 113, 113, 0.15)",
                        padding: "2px 6px",
                        borderRadius: "4px",
                      }}
                    >
                      {d.status}
                    </span>
                    <strong style={{ fontSize: "13px", color: "#fff" }}>{d.goal}</strong>
                    <code style={{ fontSize: "11px", color: "#8b949e" }}>({d.task_id})</code>
                  </div>
                  <div style={{ display: "flex", gap: "12px", fontSize: "11px", color: "#8b949e" }}>
                    <span>⏱️ {d.duration_ms}ms</span>
                    <span>🔄 {d.trace_steps} steps</span>
                  </div>
                </div>

                <p style={{ margin: "4px 0", fontSize: "12.5px", color: "#cbd5e1", lineHeight: 1.4 }}>
                  {d.summary}
                </p>

                {d.tools_used && d.tools_used.length > 0 && (
                  <div style={{ display: "flex", gap: "4px", marginTop: "4px" }}>
                    <span style={{ fontSize: "10.5px", color: "#8b949e" }}>Tools executed:</span>
                    {d.tools_used.map((t) => (
                      <span key={t} style={{ fontSize: "10px", color: "#38bdf8", background: "#21262d", padding: "1px 6px", borderRadius: "3px" }}>
                        ⚙️ {t}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 4-Agent Pipeline Section */}
      <div style={{ background: "#0d1117", border: "1px solid #30363d", borderRadius: "8px", padding: "16px" }}>
        <h3 style={{ margin: "0 0 10px 0", fontSize: "15px", color: "#fff" }}>
          🐝 4-Agent Collaborative Swarm Pipeline
        </h3>
        <div className="swarm-input-row" style={{ marginBottom: "16px" }}>
          <input
            type="text"
            value={swarmObjective}
            onChange={(e) => setSwarmObjective(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && !runningSwarm && void handleLaunchSwarm()}
            placeholder="Describe high-level engineering objective for the 4-agent swarm..."
          />
          <button
            type="button"
            className="btn-launch-swarm"
            onClick={() => void handleLaunchSwarm()}
            disabled={runningSwarm || !swarmObjective.trim()}
          >
            {runningSwarm ? "Swarm Working..." : "🚀 Launch Swarm"}
          </button>
        </div>

        <div className="swarm-pipeline-grid">
          <div className={`swarm-agent-card ${agentStates.architect}`}>
            <div className="agent-top-row">
              <span className="agent-avatar-icon">🧠</span>
              <span className={`agent-status-badge ${agentStates.architect}`}>{agentStates.architect}</span>
            </div>
            <h4 className="agent-name">Lead Architect</h4>
            <p className="agent-role-desc">Task decomposition, AST blast-radius, and ADR memory recall.</p>
          </div>

          <div className={`swarm-agent-card ${agentStates.implementer}`}>
            <div className="agent-top-row">
              <span className="agent-avatar-icon">💻</span>
              <span className={`agent-status-badge ${agentStates.implementer}`}>{agentStates.implementer}</span>
            </div>
            <h4 className="agent-name">Implementer</h4>
            <p className="agent-role-desc">Atomic file mutations with pre-flight rollback snapshots.</p>
          </div>

          <div className={`swarm-agent-card ${agentStates.verifier}`}>
            <div className="agent-top-row">
              <span className="agent-avatar-icon">🧪</span>
              <span className={`agent-status-badge ${agentStates.verifier}`}>{agentStates.verifier}</span>
            </div>
            <h4 className="agent-name">Verification & QA</h4>
            <p className="agent-role-desc">Pytest test runner, stack trace auto-healing, and E2E checks.</p>
          </div>

          <div className={`swarm-agent-card ${agentStates.auditor}`}>
            <div className="agent-top-row">
              <span className="agent-avatar-icon">🛡️</span>
              <span className={`agent-status-badge ${agentStates.auditor}`}>{agentStates.auditor}</span>
            </div>
            <h4 className="agent-name">Security Auditor</h4>
            <p className="agent-role-desc">Path traversal validation, convention audits, and conventional commits.</p>
          </div>
        </div>

        {swarmResult && (
          <div style={{ marginTop: "16px", background: "#161b22", border: "1px solid #30363d", borderRadius: "8px", padding: "14px" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <span style={{ fontSize: "14px", fontWeight: 700, color: "#34d399" }}>
                ✓ {swarmResult.status}: Session {swarmResult.session_id}
              </span>
              <span style={{ fontSize: "12px", color: "#8b949e" }}>{swarmResult.duration_ms}ms</span>
            </div>
            <p style={{ margin: "6px 0", fontSize: "12.5px", color: "#cbd5e1" }}>
              Modified files: {swarmResult.files_modified.join(", ") || "None"} • Tests passed: {swarmResult.tests_passed}/{swarmResult.tests_run}
            </p>
            {swarmResult.commit_message && (
              <div style={{ fontSize: "11.5px", color: "#38bdf8", fontFamily: "monospace" }}>
                Commit: {swarmResult.commit_message}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
