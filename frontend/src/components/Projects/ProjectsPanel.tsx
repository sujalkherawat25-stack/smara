import { useEffect, useRef, useState } from "react";
import {
  FolderOpen, Plus, Trash2, ArrowLeft, Upload, FileText,
  Loader2, CheckCircle2, XCircle, Eye, EyeOff, ArrowUp, Pencil, MessageSquare,
} from "lucide-react";
import { useProjectsStore } from "@/stores/projectsStore";
import { useViewStore } from "@/stores/viewStore";
import { useChatStore } from "@/stores/chatStore";
import { useConversationsStore } from "@/stores/conversationsStore";
import type { ProjectDocument } from "@/lib/projects";

/**
 * ProjectsPanel — F8 Projects & document corpus.
 *
 * A dedicated workspace (own full-width panel, not a chat-bar toggle),
 * mirroring the shape of Claude's Projects page: a list of Projects with
 * "+ New Project", and — once opened — a two-column workspace: a "Start a
 * chat" composer on the left (scoped to this Project for its whole
 * lifetime, via projectsStore.currentProjectId), and Instructions + Files
 * management on the right.
 */
export default function ProjectsPanel() {
  const {
    items, loaded, available, error, currentProjectId,
    refresh, create, remove, open, closeProject,
  } = useProjectsStore();

  useEffect(() => {
    void refresh();
  }, [refresh]);

  if (loaded && !available) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-2 px-6 text-center">
        <FolderOpen size={28} style={{ color: "var(--text-dim)" }} />
        <p className="text-sm" style={{ color: "var(--text-secondary)" }}>
          Projects isn't available on your plan yet.
        </p>
      </div>
    );
  }

  if (currentProjectId) {
    return <ProjectWorkspace projectId={currentProjectId} onBack={closeProject} />;
  }

  return <ProjectsList items={items} loadError={error} onCreate={create} onOpen={open} onDelete={remove} />;
}

function ProjectsList({
  items, loadError, onCreate, onOpen, onDelete,
}: {
  items: ReturnType<typeof useProjectsStore.getState>["items"];
  loadError: string | null;
  onCreate: (name: string, instructions?: string) => Promise<unknown>;
  onOpen: (id: string) => Promise<void>;
  onDelete: (id: string) => Promise<boolean>;
}) {
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    const trimmed = name.trim();
    if (!trimmed) return;
    setBusy(true);
    setError(null);
    try {
      await onCreate(trimmed);
      setName("");
      setCreating(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't create project.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-col h-full">
      <div className="px-4 py-3 shrink-0" style={{ borderBottom: "1px solid var(--border-dim)" }}>
        {creating ? (
          <div className="flex items-center gap-1.5">
            <input
              autoFocus
              className="flex-1 rounded-lg px-3 py-2 text-sm focus:outline-none"
              style={{
                background: "var(--bg-elevated)",
                border: "1px solid var(--border-default)",
                color: "var(--text-primary)",
              }}
              placeholder="Project name…"
              value={name}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") void submit();
                if (e.key === "Escape") { setCreating(false); setName(""); }
              }}
            />
            <button
              onClick={() => void submit()}
              disabled={busy}
              className="text-sm px-3 py-2 rounded-lg disabled:opacity-60"
              style={{ background: "var(--accent)", color: "#fff" }}
            >
              {busy ? "…" : "Add"}
            </button>
          </div>
        ) : (
          <button
            onClick={() => setCreating(true)}
            className="flex items-center gap-2 rounded-lg px-3 py-2 text-sm transition-colors"
            style={{
              background: "var(--bg-elevated)",
              border: "1px solid var(--border-default)",
              color: "var(--text-secondary)",
            }}
          >
            <Plus size={14} />
            New Project
          </button>
        )}
        {error && (
          <p className="mt-1.5 text-xs" style={{ color: "var(--danger, #e05252)" }}>
            {error}
          </p>
        )}
      </div>

      <div className="flex-1 overflow-y-auto px-3 py-3">
        {items.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full gap-2 px-6 text-center">
            <FolderOpen size={28} style={{ color: "var(--text-dim)" }} />
            <p className="text-sm" style={{ color: loadError ? "var(--danger, #e05252)" : "var(--text-dim)" }}>
              {loadError
                ? `Couldn't load projects: ${loadError}`
                : "No projects yet. Create one to attach documents Smara can search."}
            </p>
          </div>
        ) : (
          <ul className="grid gap-2 sm:grid-cols-2">
            {items.map((p) => (
              <li key={p.id}>
                <div
                  className="group flex items-center gap-2.5 rounded-xl px-3.5 py-3 cursor-pointer transition-colors"
                  style={{ background: "var(--bg-elevated)", border: "1px solid var(--border-dim)" }}
                  onClick={() => void onOpen(p.id)}
                >
                  <div
                    className="w-8 h-8 rounded-lg flex items-center justify-center shrink-0"
                    style={{ background: "var(--accent2-soft)" }}
                  >
                    <FolderOpen size={15} style={{ color: "var(--accent2)" }} />
                  </div>
                  <span className="flex-1 text-sm font-medium truncate" style={{ color: "var(--text-primary)" }}>
                    {p.name}
                  </span>
                  <button
                    onClick={(e) => { e.stopPropagation(); void onDelete(p.id); }}
                    className="opacity-0 group-hover:opacity-100 transition-opacity"
                    title="Delete project"
                  >
                    <Trash2 size={13} style={{ color: "var(--text-dim)" }} />
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function ProjectWorkspace({ projectId, onBack }: { projectId: string; onBack: () => void }) {
  const items = useProjectsStore((s) => s.items);
  const project = items.find((p) => p.id === projectId);
  const setView = useViewStore((s) => s.setView);
  const newChat = useChatStore((s) => s.newChat);
  const send = useChatStore((s) => s.send);

  const [draft, setDraft] = useState("");

  const startChat = () => {
    const text = draft.trim();
    if (!text) {
      // No message yet — just open the chat window, still scoped to this
      // project (currentProjectId is untouched by onBack/setView).
      setView("chat");
      return;
    }
    newChat();
    setView("chat");
    void send(text);
    setDraft("");
  };

  return (
    <div className="flex flex-col h-full">
      <div
        className="flex items-center gap-2 px-4 py-3 shrink-0"
        style={{ borderBottom: "1px solid var(--border-dim)" }}
      >
        <button onClick={onBack} title="All projects" className="flex items-center gap-1.5">
          <ArrowLeft size={14} style={{ color: "var(--text-secondary)" }} />
          <span className="text-xs" style={{ color: "var(--text-secondary)" }}>All projects</span>
        </button>
        <span className="mx-1" style={{ color: "var(--text-dim)" }}>/</span>
        <span className="text-sm font-semibold truncate" style={{ color: "var(--text-primary)" }}>
          {project?.name || "Project"}
        </span>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto">
        <div className="flex flex-col lg:flex-row gap-5 p-4 lg:p-5 max-w-5xl mx-auto">
          {/* Main column — start a chat */}
          <div className="flex-1 min-w-0 flex flex-col gap-3">
            <div
              className="rounded-2xl p-3"
              style={{ background: "var(--bg-elevated)", border: "1px solid var(--border-default)" }}
            >
              <textarea
                className="w-full bg-transparent text-sm resize-none focus:outline-none min-h-[64px]"
                style={{ color: "var(--text-primary)" }}
                placeholder="How can I help you today?"
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    startChat();
                  }
                }}
              />
              <div className="flex justify-end mt-1">
                <button
                  onClick={startChat}
                  className="w-8 h-8 rounded-full flex items-center justify-center transition-opacity"
                  style={{
                    background: draft.trim() ? "var(--accent)" : "var(--bg-surface)",
                    color: draft.trim() ? "#fff" : "var(--text-dim)",
                  }}
                  title="Start chat in this project"
                >
                  <ArrowUp size={14} />
                </button>
              </div>
            </div>
            <RecentProjectChats projectId={projectId} />
          </div>

          {/* Sidebar — Instructions + Files */}
          <div className="w-full lg:w-[320px] shrink-0 flex flex-col gap-4">
            <InstructionsCard projectId={projectId} instructions={project?.instructions ?? null} />
            <FilesCard />
          </div>
        </div>
      </div>
    </div>
  );
}

function RecentProjectChats({ projectId }: { projectId: string }) {
  // F8: purely client-side — every conversation list in this app already
  // lives in localStorage (conversationsStore), so a project's "recent
  // chats" is just a filter over the same list, no new backend call.
  const conversations = useConversationsStore((s) => s.conversations);
  const select = useConversationsStore((s) => s.select);
  const loadConversation = useChatStore((s) => s.loadConversation);
  const setView = useViewStore((s) => s.setView);

  const threads = conversations
    .filter((c) => c.projectId === projectId && c.messages.length > 0)
    .sort((a, b) => b.lastUpdated.localeCompare(a.lastUpdated));

  const open = (convId: string) => {
    const conv = select(convId);
    if (!conv) return;
    loadConversation(conv.id, conv.conversationId, conv.messages);
    setView("chat");
  };

  if (threads.length === 0) {
    return (
      <div
        className="rounded-2xl p-6 text-center text-xs"
        style={{ background: "var(--bg-elevated)", border: "1px dashed var(--border-dim)", color: "var(--text-dim)" }}
      >
        Start a chat to keep conversations organized and re-use this project's documents.
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-1.5">
      <span className="text-[11px] font-medium px-1" style={{ color: "var(--text-dim)" }}>
        Recent chats in this project
      </span>
      <ul className="flex flex-col gap-1">
        {threads.map((c) => (
          <li key={c.id}>
            <button
              onClick={() => open(c.id)}
              className="w-full flex items-center gap-2.5 rounded-xl px-3.5 py-2.5 text-left transition-colors"
              style={{ background: "var(--bg-elevated)", border: "1px solid var(--border-dim)" }}
            >
              <MessageSquare size={13} style={{ color: "var(--text-muted)", flexShrink: 0 }} />
              <span className="flex-1 text-xs truncate" style={{ color: "var(--text-primary)" }}>
                {c.title}
              </span>
              <span className="text-[10px] shrink-0" style={{ color: "var(--text-dim)" }}>
                {new Date(c.lastUpdated).toLocaleDateString()}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

function InstructionsCard({
  projectId, instructions,
}: {
  projectId: string;
  instructions: string | null;
}) {
  const updateInstructions = useProjectsStore((s) => s.updateInstructions);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(instructions ?? "");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setDraft(instructions ?? "");
  }, [instructions]);

  const save = async () => {
    setSaving(true);
    try {
      await updateInstructions(projectId, draft.trim());
      setEditing(false);
    } catch (e) {
      console.error("[projects] save instructions failed:", e);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="rounded-xl p-3.5" style={{ background: "var(--bg-elevated)", border: "1px solid var(--border-dim)" }}>
      <div className="flex items-center justify-between mb-1.5">
        <span className="text-xs font-medium" style={{ color: "var(--text-primary)" }}>Instructions</span>
        {!editing && (
          <button onClick={() => setEditing(true)} title="Edit instructions">
            {instructions ? (
              <Pencil size={12} style={{ color: "var(--text-dim)" }} />
            ) : (
              <Plus size={13} style={{ color: "var(--text-dim)" }} />
            )}
          </button>
        )}
      </div>
      {editing ? (
        <div className="flex flex-col gap-1.5">
          <textarea
            autoFocus
            className="w-full text-xs rounded-lg px-2 py-1.5 resize-none focus:outline-none min-h-[70px]"
            style={{ background: "var(--bg-surface)", border: "1px solid var(--border-default)", color: "var(--text-primary)" }}
            placeholder="Add instructions to tailor how Smara behaves in this project…"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
          />
          <div className="flex justify-end gap-1.5">
            <button
              onClick={() => { setEditing(false); setDraft(instructions ?? ""); }}
              className="text-[11px] px-2 py-1 rounded-md"
              style={{ color: "var(--text-dim)" }}
            >
              Cancel
            </button>
            <button
              onClick={() => void save()}
              disabled={saving}
              className="text-[11px] px-2 py-1 rounded-md disabled:opacity-60"
              style={{ background: "var(--accent)", color: "#fff" }}
            >
              {saving ? "Saving…" : "Save"}
            </button>
          </div>
        </div>
      ) : (
        <p className="text-[11px]" style={{ color: "var(--text-dim)" }}>
          {instructions || "Add instructions to tailor Smara's responses in this project."}
        </p>
      )}
    </div>
  );
}

function FilesCard() {
  const documents = useProjectsStore((s) => s.documents);
  const documentsLoading = useProjectsStore((s) => s.documentsLoading);
  const upload = useProjectsStore((s) => s.upload);
  const toggleActive = useProjectsStore((s) => s.toggleActive);
  const removeDocument = useProjectsStore((s) => s.removeDocument);

  const fileInputRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const onFileChosen = async (file: File | undefined) => {
    if (!file) return;
    setUploading(true);
    setError(null);
    try {
      await upload(file);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Upload failed.");
    } finally {
      setUploading(false);
    }
  };

  return (
    <div className="rounded-xl p-3.5" style={{ background: "var(--bg-elevated)", border: "1px solid var(--border-dim)" }}>
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs font-medium" style={{ color: "var(--text-primary)" }}>
          Files ({documents.length})
        </span>
        <button
          onClick={() => fileInputRef.current?.click()}
          disabled={uploading}
          title="Upload a document"
        >
          {uploading ? (
            <Loader2 size={13} className="animate-spin" style={{ color: "var(--text-dim)" }} />
          ) : (
            <Upload size={13} style={{ color: "var(--text-dim)" }} />
          )}
        </button>
        <input
          ref={fileInputRef}
          type="file"
          className="hidden"
          accept=".pdf,.docx,.xlsx,.pptx,.csv,.txt,.md,.html,.json,.xml"
          onChange={(e) => { void onFileChosen(e.target.files?.[0]); e.target.value = ""; }}
        />
      </div>

      {error && (
        <p className="text-[11px] mb-1.5" style={{ color: "var(--danger, #e05252)" }}>{error}</p>
      )}

      {documentsLoading ? (
        <div className="flex justify-center py-3">
          <Loader2 size={14} className="animate-spin" style={{ color: "var(--text-dim)" }} />
        </div>
      ) : documents.length === 0 ? (
        <p className="text-[11px]" style={{ color: "var(--text-dim)" }}>
          Add PDFs, docs, or notes for Smara to reference in this project.
        </p>
      ) : (
        <ul className="space-y-1">
          {documents.map((d) => (
            <DocumentRow
              key={d.id}
              doc={d}
              onToggle={(active) => void toggleActive(d.id, active)}
              onDelete={() => void removeDocument(d.id)}
            />
          ))}
        </ul>
      )}
    </div>
  );
}

function DocumentRow({
  doc, onToggle, onDelete,
}: {
  doc: ProjectDocument;
  onToggle: (active: boolean) => void;
  onDelete: () => void;
}) {
  const processingLabel = useProcessingLabel(doc);
  const statusIcon =
    doc.status === "ready" ? <CheckCircle2 size={11} style={{ color: "var(--accent2)" }} /> :
    doc.status === "failed" ? <XCircle size={11} style={{ color: "#e05252" }} /> :
    <Loader2 size={11} className="animate-spin" style={{ color: "var(--text-dim)" }} />;

  const statusLabel =
    doc.status === "ready" ? `Ready · ${doc.chunk_count} chunks${doc.error ? " · partial index" : ""}` :
    doc.status === "failed" ? (doc.error || "Failed") :
    processingLabel;

  return (
    <li className="group flex items-center gap-2 rounded-lg px-2 py-1.5" style={{ background: "var(--bg-surface)" }}>
      <FileText size={13} style={{ color: "var(--text-muted)", flexShrink: 0 }} />
      <div className="flex-1 min-w-0">
        <p className="text-[11px] truncate" style={{ color: "var(--text-primary)" }}>{doc.name}</p>
        <p className="flex items-center gap-1 text-[10px]" style={{ color: "var(--text-dim)" }}>
          {statusIcon}
          {statusLabel}
        </p>
        {doc.status === "ready" && doc.error && (
          <p className="text-[10px] leading-tight mt-0.5" style={{ color: "#d99a3d" }} title={doc.error}>
            {doc.error}
          </p>
        )}
      </div>
      <button
        onClick={() => onToggle(!doc.active)}
        title={doc.active ? "Mute (exclude from search)" : "Unmute"}
        className="opacity-60 hover:opacity-100 transition-opacity"
      >
        {doc.active ? (
          <Eye size={12} style={{ color: "var(--text-secondary)" }} />
        ) : (
          <EyeOff size={12} style={{ color: "var(--text-dim)" }} />
        )}
      </button>
      <button
        onClick={onDelete}
        title="Delete document"
        className="opacity-0 group-hover:opacity-100 transition-opacity"
      >
        <Trash2 size={11} style={{ color: "var(--text-dim)" }} />
      </button>
    </li>
  );
}

/**
 * Keep the otherwise static document row alive while ingestion runs. The
 * pipeline's duration is mostly driven by extracted text/chunk count, which
 * is unavailable until extraction completes, so file size provides an honest
 * approximation rather than pretending to know exact progress.
 */
function useProcessingLabel(doc: ProjectDocument): string {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (doc.status !== "processing") return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [doc.status]);

  if (doc.status !== "processing") return "Processing…";

  const startedAt = Date.parse(doc.created_at);
  if (Number.isNaN(startedAt)) return "Processing…";

  // Warm embedding normally takes about 20s for a small document. Larger
  // files tend to create more chunks, so allow ~8s per MB, capped at 3 min.
  const estimatedTotalSeconds = Math.min(
    180,
    Math.max(20, 20 + Math.ceil(doc.size_bytes / (1024 * 1024)) * 8),
  );
  const elapsedSeconds = Math.max(0, Math.floor((now - startedAt) / 1000));
  const remainingSeconds = estimatedTotalSeconds - elapsedSeconds;

  if (remainingSeconds > 0) {
    return `Processing · about ${formatDuration(remainingSeconds)} left`;
  }
  return `Processing · still working (${formatDuration(elapsedSeconds)})`;
}

function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  return `${Math.ceil(seconds / 60)} min`;
}
