import { useEffect, useState } from "react";
import { desktop, isNativeDesktop } from "../api";
import type { TaskMemorySearchItem } from "../types";

export function TaskMemoryTab({ onSetNotice }: { onSetNotice: (msg: string) => void }) {
  const [activeTarget, setActiveTarget] = useState<"memory" | "user">("memory");
  const [entries, setEntries] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [snapshot, setSnapshot] = useState<string>("");
  const [showSnapshotModal, setShowSnapshotModal] = useState(false);
  const [showAddModal, setShowAddModal] = useState(false);
  const [showReplaceModal, setShowReplaceModal] = useState(false);
  const [newContent, setNewContent] = useState("");
  const [replaceOld, setReplaceOld] = useState("");
  const [replaceNew, setReplaceNew] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<TaskMemorySearchItem[]>([]);
  const [searching, setSearching] = useState(false);
  const [filterQuery, setFilterQuery] = useState("");

  const refreshEntries = async (target = activeTarget) => {
    setLoading(true);
    try {
      if (isNativeDesktop) {
        const res = await desktop.listTaskMemory(target);
        setEntries(res?.entries || []);
      } else {
        setEntries(
          target === "memory"
            ? [
                "Testing modernized Smara agent architecture.",
                "Fast local AST graph parsing with blast-radius containment.",
                "Pytest suite runs with auto-healing and zero regressions.",
              ]
            : [
                "Prefers concise answers and zero verbose filler.",
                "Enforce strict type annotations across codebase.",
                "Never run dangerous scripts without sandbox approval.",
              ]
        );
      }
    } catch (err: any) {
      onSetNotice(`Error reading memory: ${err?.message || String(err)}`);
    } finally {
      setLoading(false);
    }
  };

  const loadSnapshot = async () => {
    try {
      if (isNativeDesktop) {
        const snap = await desktop.getMemorySnapshot(12000);
        setSnapshot(snap?.snapshot || "No memory snapshot available.");
      } else {
        setSnapshot(
          "### User Preferences & Context:\n- Prefers concise answers\n- Enforce strict typing\n\n### Curated Project Notes:\n- Modernized Smara agent architecture\n- AST blast-radius containment"
        );
      }
      setShowSnapshotModal(true);
    } catch (err: any) {
      onSetNotice(`Snapshot error: ${err?.message || String(err)}`);
    }
  };

  useEffect(() => {
    void refreshEntries(activeTarget);
  }, [activeTarget]);

  const handleAdd = async () => {
    if (!newContent.trim()) return;
    try {
      if (isNativeDesktop) {
        const res = await desktop.addTaskMemoryEntry(newContent.trim(), activeTarget);
        if (res.status === "error") {
          onSetNotice(`⚠️ ${res.message}`);
          return;
        }
        onSetNotice(`✓ Added entry to ${activeTarget === "memory" ? "MEMORY.md" : "USER.md"}`);
      } else {
        setEntries((prev) => [...prev, newContent.trim()]);
        onSetNotice(`✓ Added mock entry to ${activeTarget}`);
      }
      setNewContent("");
      setShowAddModal(false);
      await refreshEntries(activeTarget);
    } catch (err: any) {
      onSetNotice(`Add error: ${err?.message || String(err)}`);
    }
  };

  const handleReplace = async () => {
    if (!replaceOld.trim() || !replaceNew.trim()) return;
    try {
      if (isNativeDesktop) {
        const res = await desktop.replaceTaskMemoryEntry(replaceOld.trim(), replaceNew.trim(), activeTarget);
        if (res.status === "error") {
          onSetNotice(`⚠️ ${res.message}`);
          return;
        }
        onSetNotice(`✓ Updated memory entry.`);
      } else {
        setEntries((prev) => prev.map((e) => (e.includes(replaceOld) ? replaceNew : e)));
        onSetNotice(`✓ Updated mock entry.`);
      }
      setReplaceOld("");
      setReplaceNew("");
      setShowReplaceModal(false);
      await refreshEntries(activeTarget);
    } catch (err: any) {
      onSetNotice(`Replace error: ${err?.message || String(err)}`);
    }
  };

  const handleRemove = async (content: string) => {
    const sub = content.slice(0, 30);
    try {
      if (isNativeDesktop) {
        const res = await desktop.removeTaskMemoryEntry(sub, activeTarget);
        if (res.status === "error") {
          onSetNotice(`⚠️ ${res.message}`);
          return;
        }
        onSetNotice(`✓ Removed entry from memory.`);
      } else {
        setEntries((prev) => prev.filter((e) => e !== content));
        onSetNotice(`✓ Removed mock entry.`);
      }
      await refreshEntries(activeTarget);
    } catch (err: any) {
      onSetNotice(`Remove error: ${err?.message || String(err)}`);
    }
  };

  const handleSearch = async () => {
    if (!searchQuery.trim()) return;
    setSearching(true);
    try {
      if (isNativeDesktop) {
        const results = await desktop.searchTaskMemory(searchQuery.trim());
        setSearchResults(results || []);
      } else {
        setSearchResults([
          { store: "memory", content: "Fast local AST graph parsing with blast-radius containment.", relevance: 2 },
          { store: "user", content: "Never run dangerous scripts without sandbox approval.", relevance: 1 },
        ]);
      }
    } catch (err: any) {
      onSetNotice(`Search error: ${err?.message || String(err)}`);
    } finally {
      setSearching(false);
    }
  };

  const filteredEntries = entries.filter((e) =>
    !filterQuery.trim() || e.toLowerCase().includes(filterQuery.toLowerCase())
  );

  return (
    <div className="tab-pane-container">
      {/* Header */}
      <div className="pane-header">
        <div>
          <h2>🧠 Local Task Memory Manager</h2>
          <p>
            Durable, file-backed curated memory persisting across sessions with bounded snapshot caching and prompt injection defense.
          </p>
        </div>
        <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
          <button type="button" className="btn-inspect-symbol" onClick={loadSnapshot}>
            📋 System Prompt Snapshot
          </button>
          <button type="button" className="btn-new-branch" onClick={() => setShowAddModal(true)}>
            + Add Memory Entry
          </button>
          <button type="button" className="btn-refresh-git" onClick={() => void refreshEntries(activeTarget)} disabled={loading}>
            {loading ? "Refreshing..." : "🔄 Refresh"}
          </button>
        </div>
      </div>

      {/* Stats Cards */}
      <div className="dual-plane-cards-grid" style={{ marginBottom: "16px" }}>
        <div
          className={`plane-card ${activeTarget === "memory" ? "plane-local" : ""}`}
          style={{ cursor: "pointer", border: activeTarget === "memory" ? "1px solid #38bdf8" : "1px solid #30363d" }}
          onClick={() => setActiveTarget("memory")}
        >
          <div className="plane-card-header">
            <div className="plane-title-box">
              <span className="plane-badge">PROJECT STORE</span>
              <h3>MEMORY.md (Project Context)</h3>
            </div>
            <span className="status-pill pill-active">{entries.length} Entries</span>
          </div>
          <p className="plane-desc">
            Project facts, architecture decisions, CLI tool quirks, and test conventions discovered during execution.
          </p>
          <div className="plane-path-info">
            <code>~/.smara/memory/MEMORY.md</code>
          </div>
        </div>

        <div
          className={`plane-card ${activeTarget === "user" ? "plane-continuum" : ""}`}
          style={{ cursor: "pointer", border: activeTarget === "user" ? "1px solid #c084fc" : "1px solid #30363d" }}
          onClick={() => setActiveTarget("user")}
        >
          <div className="plane-card-header">
            <div className="plane-title-box">
              <span className="plane-badge badge-purple">PROFILE STORE</span>
              <h3>USER.md (User Preferences)</h3>
            </div>
            <span className="status-pill pill-active">Profile Active</span>
          </div>
          <p className="plane-desc">
            User coding style, preferred libraries, communication brevity, and workflow preferences.
          </p>
          <div className="plane-path-info">
            <code>~/.smara/memory/USER.md</code>
          </div>
        </div>
      </div>

      {/* Sub-Tabs & Filter Bar */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "12px", marginBottom: "16px" }}>
        <div style={{ display: "flex", gap: "8px" }}>
          <button
            type="button"
            className={`filter-pill ${activeTarget === "memory" ? "active" : ""}`}
            onClick={() => setActiveTarget("memory")}
          >
            📁 Project Notes ({activeTarget === "memory" ? entries.length : "..."})
          </button>
          <button
            type="button"
            className={`filter-pill ${activeTarget === "user" ? "active" : ""}`}
            onClick={() => setActiveTarget("user")}
          >
            👤 User Profile ({activeTarget === "user" ? entries.length : "..."})
          </button>
        </div>

        <div style={{ display: "flex", gap: "8px", flex: 1, maxWidth: "420px" }}>
          <input
            type="text"
            value={filterQuery}
            onChange={(e) => setFilterQuery(e.target.value)}
            placeholder="Filter current store entries..."
            style={{
              flex: 1,
              background: "#090d13",
              border: "1px solid #30363d",
              borderRadius: "6px",
              padding: "6px 10px",
              color: "#fff",
              fontSize: "12px",
            }}
          />
          {filterQuery && (
            <button
              type="button"
              onClick={() => setFilterQuery("")}
              style={{ background: "transparent", border: "1px solid #30363d", color: "#8b949e", borderRadius: "6px", padding: "0 8px" }}
            >
              Clear
            </button>
          )}
        </div>
      </div>

      {/* Memory Entries List */}
      <div style={{ display: "flex", flexDirection: "column", gap: "10px", marginBottom: "24px" }}>
        {filteredEntries.length === 0 ? (
          <div style={{ padding: "32px", textAlign: "center", color: "#8b949e", background: "#161b22", borderRadius: "8px" }}>
            No entries found in {activeTarget === "memory" ? "MEMORY.md" : "USER.md"}. Click <strong>+ Add Memory Entry</strong> to record observations.
          </div>
        ) : (
          filteredEntries.map((entry, idx) => (
            <div
              key={idx}
              style={{
                background: "#161b22",
                border: "1px solid #30363d",
                borderRadius: "8px",
                padding: "14px 18px",
                display: "flex",
                justifyContent: "space-between",
                alignItems: "flex-start",
                gap: "14px",
              }}
            >
              <div style={{ display: "flex", gap: "12px", flex: 1 }}>
                <span
                  style={{
                    fontSize: "11px",
                    fontWeight: 700,
                    color: "#38bdf8",
                    background: "rgba(56, 189, 248, 0.12)",
                    padding: "2px 8px",
                    borderRadius: "4px",
                    height: "fit-content",
                  }}
                >
                  #{idx + 1}
                </span>
                <p style={{ margin: 0, fontSize: "13px", color: "#e6edf3", lineHeight: 1.5, whiteSpace: "pre-wrap" }}>
                  {entry}
                </p>
              </div>
              <div style={{ display: "flex", gap: "6px" }}>
                <button
                  type="button"
                  title="Replace / Edit"
                  onClick={() => {
                    setReplaceOld(entry.slice(0, 40));
                    setReplaceNew(entry);
                    setShowReplaceModal(true);
                  }}
                  style={{
                    background: "transparent",
                    border: "1px solid rgba(56, 189, 248, 0.3)",
                    color: "#38bdf8",
                    padding: "4px 8px",
                    borderRadius: "4px",
                    fontSize: "12px",
                    cursor: "pointer",
                  }}
                >
                  ✏️ Edit
                </button>
                <button
                  type="button"
                  title="Remove"
                  onClick={() => void handleRemove(entry)}
                  style={{
                    background: "transparent",
                    border: "1px solid rgba(248, 81, 73, 0.3)",
                    color: "#f85149",
                    padding: "4px 8px",
                    borderRadius: "4px",
                    fontSize: "12px",
                    cursor: "pointer",
                  }}
                >
                  🗑️
                </button>
              </div>
            </div>
          ))
        )}
      </div>

      {/* Cross-Store Search Section */}
      <div style={{ background: "#0d1117", border: "1px solid #30363d", borderRadius: "8px", padding: "16px" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "12px" }}>
          <h4 style={{ margin: 0, color: "#fff", fontSize: "14px" }}>🔍 Search Across All Memory Stores</h4>
          <span style={{ fontSize: "12px", color: "#8b949e" }}>Token-relevance keyword search across MEMORY.md and USER.md</span>
        </div>
        <div style={{ display: "flex", gap: "8px", marginBottom: "12px" }}>
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && void handleSearch()}
            placeholder="Search keywords (e.g. typing, conventions, pytest, architecture)..."
            style={{
              flex: 1,
              background: "#161b22",
              border: "1px solid #30363d",
              borderRadius: "6px",
              padding: "8px 12px",
              color: "#fff",
              fontSize: "13px",
            }}
          />
          <button
            type="button"
            className="btn-create-branch-confirm"
            onClick={() => void handleSearch()}
            disabled={searching || !searchQuery.trim()}
          >
            {searching ? "Searching..." : "Search"}
          </button>
        </div>

        {searchResults.length > 0 && (
          <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
            {searchResults.map((r, i) => (
              <div
                key={i}
                style={{
                  background: "#161b22",
                  border: "1px solid #30363d",
                  borderRadius: "6px",
                  padding: "10px 14px",
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                }}
              >
                <div>
                  <span
                    style={{
                      fontSize: "10px",
                      textTransform: "uppercase",
                      padding: "2px 6px",
                      borderRadius: "4px",
                      background: r.store === "memory" ? "rgba(56, 189, 248, 0.15)" : "rgba(192, 132, 252, 0.15)",
                      color: r.store === "memory" ? "#38bdf8" : "#c084fc",
                      marginRight: "8px",
                    }}
                  >
                    {r.store}
                  </span>
                  <span style={{ fontSize: "12.5px", color: "#e6edf3" }}>{r.content}</span>
                </div>
                <span style={{ fontSize: "11px", color: "#8b949e" }}>Relevance: {r.relevance}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Add Entry Modal */}
      {showAddModal && (
        <div className="modal-backdrop" onClick={() => setShowAddModal(false)}>
          <div className="modal-dialog-card" onClick={(e) => e.stopPropagation()} style={{ maxWidth: "560px" }}>
            <h3>Add Entry to {activeTarget === "memory" ? "MEMORY.md (Project Notes)" : "USER.md (User Profile)"}</h3>
            <p>
              Entries are separated by <code>§</code> and automatically scanned against suspicious prompt injection patterns.
            </p>
            <div style={{ display: "flex", gap: "10px", margin: "10px 0" }}>
              <label style={{ display: "flex", alignItems: "center", gap: "6px", fontSize: "12.5px", color: "#cbd5e1" }}>
                <input
                  type="radio"
                  name="targetStore"
                  checked={activeTarget === "memory"}
                  onChange={() => setActiveTarget("memory")}
                />
                Project Notes (MEMORY.md)
              </label>
              <label style={{ display: "flex", alignItems: "center", gap: "6px", fontSize: "12.5px", color: "#cbd5e1" }}>
                <input
                  type="radio"
                  name="targetStore"
                  checked={activeTarget === "user"}
                  onChange={() => setActiveTarget("user")}
                />
                User Profile (USER.md)
              </label>
            </div>
            <textarea
              rows={4}
              value={newContent}
              onChange={(e) => setNewContent(e.target.value)}
              placeholder="e.g. Always run 'pytest -k unit' before modifying database migrations..."
              style={{
                background: "#090d13",
                border: "1px solid #30363d",
                borderRadius: "8px",
                padding: "10px 12px",
                color: "#fff",
                fontSize: "13px",
                fontFamily: "inherit",
              }}
            />
            <div style={{ fontSize: "11px", color: "#34d399", display: "flex", alignItems: "center", gap: "4px" }}>
              🛡️ Threat filter active: Inputs scanning for prompt injection & credential leakage.
            </div>
            <div className="modal-actions">
              <button type="button" onClick={() => setShowAddModal(false)}>Cancel</button>
              <button
                type="button"
                className="btn-create-branch-confirm"
                onClick={() => void handleAdd()}
                disabled={!newContent.trim()}
              >
                Save Memory Entry
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Replace / Edit Modal */}
      {showReplaceModal && (
        <div className="modal-backdrop" onClick={() => setShowReplaceModal(false)}>
          <div className="modal-dialog-card" onClick={(e) => e.stopPropagation()} style={{ maxWidth: "560px" }}>
            <h3>Substring Replace in {activeTarget === "memory" ? "MEMORY.md" : "USER.md"}</h3>
            <p>
              Locates the entry containing the target substring and updates it atomically without fragile line numbers.
            </p>
            <div style={{ display: "flex", flexDirection: "column", gap: "8px", margin: "10px 0" }}>
              <span style={{ fontSize: "12px", color: "#8b949e" }}>Unique Substring Identifier:</span>
              <input
                type="text"
                value={replaceOld}
                onChange={(e) => setReplaceOld(e.target.value)}
                placeholder="Target substring in existing entry..."
              />
              <span style={{ fontSize: "12px", color: "#8b949e", marginTop: "6px" }}>New Entry Content:</span>
              <textarea
                rows={4}
                value={replaceNew}
                onChange={(e) => setReplaceNew(e.target.value)}
                placeholder="Updated entry text..."
                style={{
                  background: "#090d13",
                  border: "1px solid #30363d",
                  borderRadius: "8px",
                  padding: "10px 12px",
                  color: "#fff",
                  fontSize: "13px",
                  fontFamily: "inherit",
                }}
              />
            </div>
            <div className="modal-actions">
              <button type="button" onClick={() => setShowReplaceModal(false)}>Cancel</button>
              <button
                type="button"
                className="btn-create-branch-confirm"
                onClick={() => void handleReplace()}
                disabled={!replaceOld.trim() || !replaceNew.trim()}
              >
                Replace Entry
              </button>
            </div>
          </div>
        </div>
      )}

      {/* System Prompt Snapshot Modal */}
      {showSnapshotModal && (
        <div className="modal-backdrop" onClick={() => setShowSnapshotModal(false)}>
          <div className="modal-dialog-card" onClick={(e) => e.stopPropagation()} style={{ maxWidth: "680px" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <h3>📋 System Prompt Memory Snapshot</h3>
              <span style={{ fontSize: "11px", color: "#34d399", background: "rgba(16, 185, 129, 0.15)", padding: "2px 8px", borderRadius: "10px" }}>
                Prefix-Cache Friendly
              </span>
            </div>
            <p>
              This exact bounded markdown block is injected into the system prompt when an agent session begins.
            </p>
            <pre
              style={{
                background: "#090d13",
                border: "1px solid #30363d",
                borderRadius: "8px",
                padding: "12px",
                maxHeight: "360px",
                overflowY: "auto",
                fontSize: "12px",
                whiteSpace: "pre-wrap",
                color: "#e6edf3",
                fontFamily: "Consolas, monospace",
                lineHeight: 1.5,
              }}
            >
              {snapshot}
            </pre>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <span style={{ fontSize: "11.5px", color: "#8b949e" }}>
                Total length: {snapshot.length} / 12,000 characters
              </span>
              <button type="button" onClick={() => setShowSnapshotModal(false)}>
                Close
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
