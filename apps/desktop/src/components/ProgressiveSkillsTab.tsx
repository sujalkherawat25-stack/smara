import { useEffect, useState } from "react";
import { desktop, isNativeDesktop } from "../api";
import type { ProgressiveSkillDetail, ProgressiveSkillItem } from "../types";

export function ProgressiveSkillsTab({ onSetNotice }: { onSetNotice: (msg: string) => void }) {
  const [skills, setSkills] = useState<ProgressiveSkillItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectedSkillName, setSelectedSkillName] = useState<string | null>(null);
  const [skillDetail, setSkillDetail] = useState<ProgressiveSkillDetail | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [selectedAsset, setSelectedAsset] = useState<string | null>(null);
  const [assetContent, setAssetContent] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [tagFilter, setTagFilter] = useState<string>("all");
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [newName, setNewName] = useState("");
  const [newDesc, setNewDesc] = useState("");
  const [newTags, setNewTags] = useState("");
  const [newInstructions, setNewInstructions] = useState("");
  const [lifecycleRows, setLifecycleRows] = useState<any[]>([]);
  const [lifecycleBusy, setLifecycleBusy] = useState(false);
  const [rollbackVersion, setRollbackVersion] = useState("");

  const refreshSkills = async () => {
    setLoading(true);
    try {
      if (isNativeDesktop) {
        const [data, lifecycle] = await Promise.all([desktop.listSkillsV2(), desktop.skillLifecycle().catch(() => [])]);
        setSkills(data || []);
        setLifecycleRows(lifecycle || []);
        if (data && data.length > 0 && !selectedSkillName) {
          void loadSkillDetail(data[0].name);
        }
      } else {
        setSkills([]);
      }
    } catch (err: any) {
      onSetNotice(`Error listing skills: ${err?.message || String(err)}`);
    } finally {
      setLoading(false);
    }
  };

  const selectedLifecycle = lifecycleRows.find((row) => row.name === selectedSkillName);
  const runLifecycle = async (action: "validate" | "promote" | "revoke" | "rollback") => {
    if (!selectedSkillName) return;
    setLifecycleBusy(true);
    try {
      const version = skillDetail?.metadata?.version || undefined;
      if (action === "validate") await desktop.validateSkillV2(selectedSkillName, version);
      if (action === "promote") await desktop.promoteSkillV2(selectedSkillName, version);
      if (action === "revoke") await desktop.revokeSkillV2(selectedSkillName, version);
      if (action === "rollback") {
        if (!rollbackVersion) throw new Error("Choose a retained version to roll back to.");
        await desktop.rollbackSkillV2(selectedSkillName, rollbackVersion);
      }
      onSetNotice(`Skill ${selectedSkillName} ${action} completed.`);
      await refreshSkills();
      await loadSkillDetail(selectedSkillName);
    } catch (err: any) {
      onSetNotice(`Skill ${action} failed: ${err?.message || String(err)}`);
    } finally {
      setLifecycleBusy(false);
    }
  };

  const loadSkillDetail = async (skillName: string) => {
    setSelectedSkillName(skillName);
    setSelectedAsset(null);
    setAssetContent(null);
    setLoadingDetail(true);
    try {
      if (isNativeDesktop) {
        const detail = await desktop.viewSkillV2(skillName);
        setSkillDetail(detail);
      } else {
        setSkillDetail(null);
      }
    } catch (err: any) {
      onSetNotice(`Error viewing skill: ${err?.message || String(err)}`);
    } finally {
      setLoadingDetail(false);
    }
  };

  const loadAsset = async (assetPath: string) => {
    if (!selectedSkillName) return;
    setSelectedAsset(assetPath);
    try {
      if (isNativeDesktop) {
        const res = await desktop.viewSkillV2(selectedSkillName, assetPath);
        setAssetContent(res?.content || "No content returned.");
      } else {
        setAssetContent(null);
      }
    } catch (err: any) {
      onSetNotice(`Error loading asset: ${err?.message || String(err)}`);
    }
  };

  useEffect(() => {
    void refreshSkills();
  }, []);

  const handleCreateSkill = async () => {
    if (!newName.trim() || !newDesc.trim() || !newInstructions.trim()) return;
    try {
      const tagsList = newTags.split(",").map((t) => t.trim()).filter(Boolean);
      if (!isNativeDesktop) {
        throw new Error("Skill creation requires running inside the Smara Desktop native application.");
      }
      const res = await desktop.createSkillV2(newName.trim(), newDesc.trim(), tagsList, newInstructions.trim());
      if (res.status === "error") {
        onSetNotice(`⚠️ ${res.message}`);
        return;
      }
      onSetNotice(`✓ Created progressive skill '${newName.trim()}' with SKILL.md`);
      setShowCreateModal(false);
      setNewName("");
      setNewDesc("");
      setNewTags("");
      setNewInstructions("");
      await refreshSkills();
      await loadSkillDetail(newName.trim());
    } catch (err: any) {
      onSetNotice(`Create skill error: ${err?.message || String(err)}`);
    }
  };

  const allTags = Array.from(new Set(skills.flatMap((s) => s.tags || [])));

  const filteredSkills = skills.filter((s) => {
    const matchesSearch =
      !searchQuery.trim() ||
      s.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      s.description.toLowerCase().includes(searchQuery.toLowerCase()) ||
      s.tags.some((t) => t.toLowerCase().includes(searchQuery.toLowerCase()));
    const matchesTag = tagFilter === "all" || s.tags.includes(tagFilter);
    return matchesSearch && matchesTag;
  });

  return (
    <div className="tab-pane-container">
      {/* Header */}
      <div className="pane-header">
        <div>
          <h2>📚 Progressive Disclosure Skills System</h2>
          <p>
            3-Tier on-demand execution: Tier 1 lightweight metadata catalog → Tier 2 full SKILL.md markdown instructions → Tier 3 referenced assets & examples.
          </p>
        </div>
        <div style={{ display: "flex", gap: "8px" }}>
          <button type="button" className="btn-new-branch" onClick={() => setShowCreateModal(true)}>
            + Create New Skill
          </button>
          <button type="button" className="btn-refresh-git" onClick={refreshSkills} disabled={loading}>
            {loading ? "Refreshing..." : "🔄 Refresh"}
          </button>
        </div>
      </div>

      {/* Main Split Layout */}
      <div style={{ display: "grid", gridTemplateColumns: "360px 1fr", gap: "16px", minHeight: "560px" }}>
        {/* Left Pane: Tier 1 Catalog */}
        <div style={{ background: "#111827", border: "1px solid #30363d", borderRadius: "8px", padding: "14px", display: "flex", flexDirection: "column", gap: "10px" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <span style={{ fontSize: "12px", fontWeight: 700, color: "#38bdf8", textTransform: "uppercase" }}>
              Tier 1: Catalog ({filteredSkills.length})
            </span>
            <span style={{ fontSize: "11px", color: "#8b949e" }}>Zero-Token Overhead</span>
          </div>

          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search skills by name, trigger, tag..."
            style={{
              background: "#090d13",
              border: "1px solid #30363d",
              borderRadius: "6px",
              padding: "7px 10px",
              color: "#fff",
              fontSize: "12px",
            }}
          />

          {allTags.length > 0 && (
            <div style={{ display: "flex", flexWrap: "wrap", gap: "4px" }}>
              <button
                type="button"
                className={`filter-pill ${tagFilter === "all" ? "active" : ""}`}
                style={{ fontSize: "10px", padding: "2px 6px" }}
                onClick={() => setTagFilter("all")}
              >
                All
              </button>
              {allTags.map((t) => (
                <button
                  key={t}
                  type="button"
                  className={`filter-pill ${tagFilter === t ? "active" : ""}`}
                  style={{ fontSize: "10px", padding: "2px 6px" }}
                  onClick={() => setTagFilter(t)}
                >
                  #{t}
                </button>
              ))}
            </div>
          )}

          <div style={{ display: "flex", flexDirection: "column", gap: "8px", overflowY: "auto", maxHeight: "480px" }}>
            {filteredSkills.map((s) => {
              const isSelected = selectedSkillName === s.name;
              const sourceColor =
                s.source === "workspace" ? "#38bdf8" : s.source === "user" ? "#c084fc" : "#34d399";
              return (
                <div
                  key={s.name}
                  onClick={() => void loadSkillDetail(s.name)}
                  style={{
                    background: isSelected ? "rgba(56, 189, 248, 0.12)" : "#161b22",
                    border: isSelected ? "1px solid #38bdf8" : "1px solid #30363d",
                    borderRadius: "6px",
                    padding: "10px 12px",
                    cursor: "pointer",
                    display: "flex",
                    flexDirection: "column",
                    gap: "6px",
                  }}
                >
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                    <strong style={{ fontSize: "13px", color: isSelected ? "#38bdf8" : "#fff", fontFamily: "monospace" }}>
                      {s.name}
                    </strong>
                    <span
                      style={{
                        fontSize: "9.5px",
                        fontWeight: 700,
                        textTransform: "uppercase",
                        color: sourceColor,
                        background: `${sourceColor}20`,
                        padding: "2px 6px",
                        borderRadius: "4px",
                      }}
                    >
                      {s.source}
                    </span>
                  </div>
                  <div style={{ display: "flex", gap: "5px", alignItems: "center", fontSize: "10px", color: s.lifecycle === "promoted" ? "#34d399" : "#fbbf24" }}>
                    <span>v{s.version}</span>
                    <span>·</span>
                    <span>{s.lifecycle === "promoted" ? "verified" : (s.lifecycle || "unreviewed")}</span>
                  </div>
                  <p style={{ margin: 0, fontSize: "11.5px", color: "#8b949e", lineHeight: 1.4 }}>
                    {s.description}
                  </p>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: "4px" }}>
                    {(s.tags || []).map((t) => (
                      <span key={t} style={{ fontSize: "9.5px", color: "#94a3b8", background: "#21262d", padding: "1px 5px", borderRadius: "3px" }}>
                        #{t}
                      </span>
                    ))}
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        {/* Right Pane: Tier 2 & Tier 3 Viewer */}
        <div style={{ background: "#111827", border: "1px solid #30363d", borderRadius: "8px", padding: "16px", display: "flex", flexDirection: "column", gap: "12px" }}>
          {loadingDetail ? (
            <div style={{ padding: "40px", textAlign: "center", color: "#8b949e" }}>Loading progressive skill instructions...</div>
          ) : !skillDetail ? (
            <div style={{ padding: "40px", textAlign: "center", color: "#8b949e" }}>Select a skill to inspect its instructions.</div>
          ) : (
            <>
              {/* Skill Top Bar */}
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", borderBottom: "1px solid #30363d", paddingBottom: "12px" }}>
                <div>
                  <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                    <h3 style={{ margin: 0, color: "#fff", fontSize: "16px", fontFamily: "monospace" }}>
                      {skillDetail.skill}
                    </h3>
                    <span style={{ fontSize: "11px", color: "#34d399", background: "rgba(16, 185, 129, 0.15)", padding: "2px 8px", borderRadius: "6px" }}>
                      v{skillDetail.metadata?.version || "1.0.0"}
                    </span>
                  </div>
                  <p style={{ margin: "4px 0 0 0", fontSize: "12.5px", color: "#8b949e" }}>
                    {skillDetail.metadata?.description}
                  </p>
                </div>
                <button
                  type="button"
                  className="btn-inspect-symbol"
                  onClick={() => onSetNotice(`Skill '${skillDetail.skill}' ready for execution in chat session.`)}
                >
                  ⚡ Use in Session
                </button>
              </div>

              <div style={{ display: "flex", flexWrap: "wrap", gap: "8px", alignItems: "center", padding: "10px", background: "rgba(15, 23, 42, .6)", border: "1px solid #30363d", borderRadius: "8px" }}>
                <span style={{ fontSize: "11px", color: selectedLifecycle?.state === "promoted" ? "#34d399" : "#fbbf24" }}>
                  Lifecycle: <strong>{selectedLifecycle?.state || "unreviewed"}</strong>
                </span>
                <button type="button" className="filter-pill" disabled={lifecycleBusy} onClick={() => void runLifecycle("validate")}>Validate</button>
                <button type="button" className="filter-pill" disabled={lifecycleBusy} onClick={() => void runLifecycle("promote")}>Promote</button>
                <button type="button" className="filter-pill" disabled={lifecycleBusy} onClick={() => void runLifecycle("revoke")}>Revoke</button>
                {(selectedLifecycle?.versions || []).length > 1 && <>
                  <select value={rollbackVersion} onChange={(event) => setRollbackVersion(event.target.value)} aria-label="Rollback skill version">
                    <option value="">Rollback version…</option>
                    {(selectedLifecycle.versions || []).map((version: string) => <option key={version} value={version}>{version}</option>)}
                  </select>
                  <button type="button" className="filter-pill" disabled={lifecycleBusy || !rollbackVersion} onClick={() => void runLifecycle("rollback")}>Rollback</button>
                </>}
                {selectedLifecycle?.validation && <span style={{ width: "100%", fontSize: "10px", color: selectedLifecycle.validation.passed ? "#86efac" : "#fca5a5" }}>
                  Validation: {selectedLifecycle.validation.passed ? "passed" : (selectedLifecycle.validation.failures || []).join(", ")}
                </span>}
              </div>

              {/* Tier 2 / 3 Navigation Bar */}
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <div style={{ display: "flex", gap: "8px" }}>
                  <button
                    type="button"
                    className={`filter-pill ${selectedAsset === null ? "active" : ""}`}
                    onClick={() => {
                      setSelectedAsset(null);
                      setAssetContent(null);
                    }}
                  >
                    📖 Tier 2: SKILL.md
                  </button>
                  {skillDetail.available_assets && skillDetail.available_assets.length > 0 && (
                    <span style={{ display: "flex", gap: "6px", alignItems: "center", marginLeft: "8px" }}>
                      <span style={{ fontSize: "11px", color: "#8b949e" }}>Tier 3 Assets:</span>
                      {skillDetail.available_assets.map((asset) => (
                        <button
                          key={asset}
                          type="button"
                          className={`filter-pill ${selectedAsset === asset ? "active" : ""}`}
                          onClick={() => void loadAsset(asset)}
                          style={{ fontSize: "11px" }}
                        >
                          📄 {asset}
                        </button>
                      ))}
                    </span>
                  )}
                </div>
              </div>

              {/* Markdown Content Pane */}
              <div
                style={{
                  background: "#090d13",
                  border: "1px solid #30363d",
                  borderRadius: "6px",
                  padding: "16px",
                  flex: 1,
                  overflowY: "auto",
                  maxHeight: "440px",
                }}
              >
                {selectedAsset && assetContent ? (
                  <div>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "10px", paddingBottom: "6px", borderBottom: "1px solid #21262d" }}>
                      <span style={{ fontSize: "12px", color: "#38bdf8", fontFamily: "monospace" }}>Tier 3 Asset: {selectedAsset}</span>
                      <button
                        type="button"
                        onClick={() => {
                          setSelectedAsset(null);
                          setAssetContent(null);
                        }}
                        style={{ background: "transparent", border: "none", color: "#8b949e", cursor: "pointer", fontSize: "11px" }}
                      >
                        ← Back to SKILL.md
                      </button>
                    </div>
                    <pre style={{ margin: 0, fontSize: "12px", color: "#e6edf3", fontFamily: "Consolas, monospace", whiteSpace: "pre-wrap", lineHeight: 1.5 }}>
                      {assetContent}
                    </pre>
                  </div>
                ) : (
                  <pre style={{ margin: 0, fontSize: "12px", color: "#e6edf3", fontFamily: "Consolas, monospace", whiteSpace: "pre-wrap", lineHeight: 1.5 }}>
                    {skillDetail.instructions}
                  </pre>
                )}
              </div>
            </>
          )}
        </div>
      </div>

      {/* Create Skill Modal */}
      {showCreateModal && (
        <div className="modal-backdrop" onClick={() => setShowCreateModal(false)}>
          <div className="modal-dialog-card" onClick={(e) => e.stopPropagation()} style={{ maxWidth: "600px" }}>
            <h3>Create New Progressive Skill</h3>
            <p>
              Scaffolds a skill directory under <code>.smara/skills/&lt;name&gt;/SKILL.md</code> with standard YAML frontmatter for Tier 1 discovery.
            </p>
            <div style={{ display: "flex", flexDirection: "column", gap: "10px", margin: "10px 0" }}>
              <input
                type="text"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                placeholder="Skill name (e.g., git-rebase-conflict-resolver)"
              />
              <input
                type="text"
                value={newDesc}
                onChange={(e) => setNewDesc(e.target.value)}
                placeholder="Short description of what the skill does..."
              />
              <input
                type="text"
                value={newTags}
                onChange={(e) => setNewTags(e.target.value)}
                placeholder="Tags comma-separated (e.g. git, conflicts, automated)"
              />
              <textarea
                rows={8}
                value={newInstructions}
                onChange={(e) => setNewInstructions(e.target.value)}
                placeholder="# Procedure Steps&#10;&#10;1. Inspect working directory conflicts&#10;2. Apply 3-way merge heuristics&#10;3. Verify test suite passes without regressions"
                style={{
                  background: "#090d13",
                  border: "1px solid #30363d",
                  borderRadius: "8px",
                  padding: "10px 12px",
                  color: "#fff",
                  fontSize: "13px",
                  fontFamily: "Consolas, monospace",
                }}
              />
            </div>
            <div className="modal-actions">
              <button type="button" onClick={() => setShowCreateModal(false)}>Cancel</button>
              <button
                type="button"
                className="btn-create-branch-confirm"
                onClick={() => void handleCreateSkill()}
                disabled={!newName.trim() || !newDesc.trim() || !newInstructions.trim()}
              >
                Create Skill
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
