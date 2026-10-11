import { useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";

type ChangedFile = { path: string; hunks: number; added: number; removed: number };
type Review = { token: string; files: ChangedFile[]; view: string; notes: string[]; base?: string; bases?: string[] };
type FileDiff = { path: string; patch: string };
type Action = "stage" | "unstage" | "revert" | "undo";
export const reviewRequest = <T,>(workspace: string, operation: string, values: Record<string, unknown> = {}) =>
  invoke<T>("native_review", { request: { workspace, operation, ...values } });

export function diffParts(patch: string): { header: string; hunks: string[] } {
  const lines = patch.match(/[^\n]*\n|[^\n]+$/g) ?? [];
  const starts = lines.flatMap((line, index) => line.startsWith("@@ ") ? [index] : []);
  return { header: lines.slice(0, starts[0] ?? lines.length).join(""), hunks: starts.map((start, index) => lines.slice(start, starts[index + 1] ?? lines.length).join("")) };
}

export default function NativeChanges({ workspace, threadId, locked, revision, notice, onBusyChange, onClose }: {
  workspace: string; threadId: string; locked: boolean; revision: number; notice: string;
  onBusyChange: (busy: boolean) => void; onClose: () => void;
}) {
  const [view, setView] = useState("unstaged");
  const [base, setBase] = useState("");
  const [review, setReview] = useState<Review | null>(null);
  const [file, setFile] = useState<FileDiff | null>(null);
  const [loading, setLoading] = useState(false);
  const [applying, setApplying] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [recovery, setRecovery] = useState("");
  const requestId = useRef(0);
  const applyingRef = useRef(false);
  const load = async () => {
    const id = ++requestId.current;
    setLoading(true); setError(""); setFile(null); setReview(null);
    try {
      const result = await reviewRequest<Review>(workspace, "status", { view, thread_id: threadId, base: base || undefined });
      if (id === requestId.current) setReview(result);
    } catch (reason) { if (id === requestId.current) setError(String(reason)); }
    finally { if (id === requestId.current) setLoading(false); }
  };
  useEffect(() => {
    void load();
    return () => { requestId.current++; };
  }, [workspace, threadId, view, base, revision]);
  const select = async (path: string) => {
    if (!review || applyingRef.current) return;
    const id = ++requestId.current;
    setLoading(true); setFile(null); setError("");
    try {
      const result = await reviewRequest<FileDiff>(workspace, "file", { token: review.token, path });
      if (id === requestId.current) setFile(result);
    } catch (reason) { if (id === requestId.current) setError(String(reason)); }
    finally { if (id === requestId.current) setLoading(false); }
  };
  const apply = async (action: Action, path?: string, hunk?: number) => {
    if (locked || view === "branch" || applyingRef.current || !review?.token || loading) return;
    const paths = path ? [path] : review.files.map(f => f.path);
    const destructive = action === "revert" || action === "undo";
    const explanation = `${action[0].toUpperCase() + action.slice(1)} ${hunk !== undefined ? "this hunk in" : "changes in"} ${paths.join(", ")}?${destructive ? " A local recovery copy will be kept. Conflicting later edits are refused, not overwritten." : " Only the Git index is changed."}${action === "undo" ? " This reverses file changes, not chat history; the index is left unchanged." : ""}`;
    if (!window.confirm(explanation)) return;
    applyingRef.current = true; setApplying(true); onBusyChange(true); setError(""); setMessage("");
    try {
      const result = await reviewRequest<{ message: string; recovery?: string }>(workspace, "apply", { action, token: review.token, path, hunk, confirmed: true });
      setMessage(`${result.message}${result.recovery ? ` Recovery copy: ${result.recovery}` : ""}`);
      setRecovery(result.recovery ?? "");
      await load();
    } catch (reason) { setError(`${String(reason)} No automatic retry; refresh to inspect the actual state.`); }
    finally { applyingRef.current = false; setApplying(false); onBusyChange(false); }
  };
  const recover = async () => {
    if (locked || applyingRef.current || !recovery || loading || !window.confirm("Restore files from the last local recovery copy? Files or index changes made since that action will block restoration.")) return;
    applyingRef.current = true; setApplying(true); onBusyChange(true); setError("");
    try {
      const result = await reviewRequest<{ message: string }>(workspace, "recover", { recovery, confirmed: true });
      setRecovery(""); setMessage(result.message); await load();
    } catch (reason) { setError(`${String(reason)} No automatic retry.`); }
    finally { applyingRef.current = false; setApplying(false); onBusyChange(false); }
  };
  const parts = diffParts(file?.patch ?? "");
  const fileOnly = parts.header.includes("file mode");
  const disabled = locked || applying || loading;
  const primary: Action = view === "staged" ? "unstage" : view === "turn" ? "undo" : "stage";
  const label = { stage: "Stage", unstage: "Unstage", revert: "Revert", undo: "Undo" };
  return <section className="changes-panel" aria-label="Changes review">
    <div className="panel-heading"><strong>Changes</strong><button disabled={applying} aria-label="Close changes" onClick={onClose}>×</button></div>
    <nav className="review-tabs" aria-label="Changes views">{[{ id: "unstaged", label: "Unstaged" }, { id: "staged", label: "Staged" }, { id: "turn", label: "Last turn" }, { id: "branch", label: "Branch" }].map(tab => <button key={tab.id} className={view === tab.id ? "selected" : ""} disabled={applying} onClick={() => { if (!applyingRef.current) setView(tab.id); }}>{tab.label}</button>)}</nav>
    {view === "branch" && !!review?.bases?.length && <label className="review-note">Compare with <select aria-label="Comparison branch" value={base || review.base || ""} onChange={event => setBase(event.target.value)}><option value="HEAD">HEAD</option>{review.bases.map(ref => <option key={ref} value={ref}>{ref}</option>)}</select></label>}
    <div className="review-toolbar"><button disabled={loading || applying} onClick={() => { if (!applyingRef.current) void load(); }}>Refresh</button>{view !== "branch" && !!review?.files.length && <button disabled={disabled} onClick={() => void apply(primary)}>{label[primary]} all ({review.files.length})</button>}{recovery && <button disabled={disabled} onClick={() => void recover()}>Restore last recovery</button>}</div>
    {locked && <p className="review-note">Review is read-only while Smara or its workers are running.</p>}
    {notice && <p className="review-note">{notice}</p>}
    {error && <p role="alert" className="review-error">{error}</p>}{message && <p role="status" className="review-note">{message}</p>}
    {review?.notes.map(note => <p className="review-note" key={note}>{note}</p>)}
    <div className="review-body"><div className="changed-files">{review?.files.map(value => <button key={value.path} className={file?.path === value.path ? "selected" : ""} disabled={applying} onClick={() => void select(value.path)}><span>{value.path}</span><small className="diff-count"><span>+{value.added}</span> −{value.removed}</small></button>)}{review && !review.files.length && <p>No reviewable changes.</p>}</div>
      <div className="file-diff">{loading && <p role="status">Loading changes…</p>}{file ? <><div className="file-actions"><strong>{file.path}</strong>{view !== "branch" && <button disabled={disabled} onClick={() => void apply(primary, file.path)}>{label[primary]} file</button>}{view === "unstaged" && <button disabled={disabled} onClick={() => void apply("revert", file.path)}>Revert file</button>}</div>
        <pre className="diff-header">{parts.header}</pre>{parts.hunks.map((hunk, index) => <section className="diff-hunk" key={index}><div className="hunk-actions"><small>Hunk {index + 1}</small>{view !== "branch" && <button disabled={disabled || fileOnly} title={fileOnly ? "New/deleted files and mode changes need a whole-file action" : undefined} onClick={() => void apply(primary, file.path, index)}>{label[primary]} hunk</button>}{view === "unstaged" && <button disabled={disabled || fileOnly} onClick={() => void apply("revert", file.path, index)}>Revert hunk</button>}</div><pre>{hunk.split("\n").map((line, i) => <span key={i} className={line.startsWith("+") ? "diff-add" : line.startsWith("-") ? "diff-remove" : line.startsWith("@@") ? "diff-position" : ""}>{line || " "}</span>)}</pre></section>)}
      </> : !loading && !!review?.files.length && <p>Select a file to review its diff.</p>}</div></div>
  </section>;
}
