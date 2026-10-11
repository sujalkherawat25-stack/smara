import { useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import type { ContextFile, ProjectListing } from "./nativeContext";

export default function NativeContextPicker({ workspace, files, onAttach, onClose }: {
  workspace: string; files: ContextFile[]; onAttach: (files: ContextFile[]) => void; onClose: () => void;
}) {
  const [folder, setFolder] = useState("");
  const [path, setPath] = useState("");
  const [listing, setListing] = useState<ProjectListing | null>(null);
  const [selected, setSelected] = useState(files);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let current = true;
    setListing(null); setError("");
    void invoke<ProjectListing>("native_project_files", { workspace, path: folder })
      .then(result => { if (current) { setListing(result); setPath(result.path); } })
      .catch(reason => { if (current) setError(String(reason)); });
    return () => { current = false; };
  }, [workspace, folder, revision]);
  useEffect(() => {
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    window.addEventListener("keydown", escape);
    return () => window.removeEventListener("keydown", escape);
  }, [onClose]);
  const toggle = (file: ContextFile) => setSelected(current => current.some(item => item.path === file.path)
    ? current.filter(item => item.path !== file.path) : [...current, file]);
  return <div className="native-preview-backdrop" onMouseDown={event => { if (event.target === event.currentTarget) onClose(); }}>
    <section role="dialog" aria-modal="true" aria-labelledby="context-title" className="native-context-dialog" onKeyDown={event => {
      if (event.key !== "Tab") return;
      const controls = event.currentTarget.querySelectorAll<HTMLElement>("button:not([disabled]), input:not([disabled])");
      const first = controls[0], last = controls[controls.length - 1];
      const active = event.currentTarget.ownerDocument.activeElement;
      if (event.shiftKey && active === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && active === last) { event.preventDefault(); first?.focus(); }
    }}>
      <header><div><strong id="context-title">Add project files</strong><small>{workspace}</small></div><button type="button" aria-label="Close file picker" onClick={onClose}>×</button></header>
      <form onSubmit={event => { event.preventDefault(); setFolder(path); setRevision(value => value + 1); }}><label>Folder or file path<input autoFocus aria-label="Project context path" value={path} placeholder="Project root" onChange={event => setPath(event.target.value)} /></label><button type="submit">Go</button><button type="button" onClick={() => { setFolder(""); setPath(""); setRevision(value => value + 1); }}>Project root</button></form>
      {error && <p role="alert">{error}</p>}
      <div className="native-context-files">
        {listing?.path && <button type="button" onClick={() => setFolder(listing.path.split("/").slice(0, -1).join("/"))}>↰ Parent folder</button>}
        {!listing && !error && <p role="status">Loading project files…</p>}
        {listing?.entries.map(file => <button type="button" key={file.path} aria-pressed={!file.directory && selected.some(item => item.path === file.path)} onClick={() => file.directory ? setFolder(file.path) : toggle(file)}><span>{file.directory ? "▱" : selected.some(item => item.path === file.path) ? "✓" : "＋"}</span><span>{file.name}</span><small>{file.directory ? "Folder" : `${Math.ceil(file.size / 1024)} KB`}</small></button>)}
        {listing && !listing.entries.length && <p>This folder is empty.</p>}
        {listing?.truncated && <p>Showing 1,000 entries. Enter a more specific folder or file path above.</p>}
      </div>
      <footer><small>Selected files may be read and shared with your chosen model for this task. Image/OCR uploads ask for approval. Select only files you want to share.</small><button type="button" className="primary" onClick={() => { onAttach(selected); onClose(); }}>{selected.length ? `Attach ${selected.length} ${selected.length === 1 ? "file" : "files"}` : "Done"}</button></footer>
    </section>
  </div>;
}
