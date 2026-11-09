import { useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";

type Entry = { name: string; path: string; directory: boolean; size: number };
type Listing = { path: string; entries: Entry[]; truncated: boolean };

/** Browse the selected project's files (names and sizes only; contents open in the preview). */
export default function NativeFiles({ workspace, onPreview }: { workspace: string; onPreview: (path: string) => void }) {
  const [folder, setFolder] = useState("");
  const [listing, setListing] = useState<Listing | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    setError("");
    if (!workspace) { setListing(null); return; }
    void invoke<Listing>("native_project_files", { workspace, path: folder })
      .then(setListing)
      .catch(reason => { setListing(null); setError(String(reason)); });
  }, [workspace, folder]);
  const up = () => setFolder(current => current.split("/").slice(0, -1).join("/"));
  return (
    <div className="native-files" aria-label="Project files">
      <div className="tool-output-actions">
        <button className="text-button" disabled={!folder} onClick={up}>↑ Up</button>
        <code>{folder || "/"}</code>
      </div>
      {error && <p role="alert">{error}</p>}
      <ul>
        {listing?.entries.map(entry => <li key={entry.path}>
          {entry.directory
            ? <button className="text-button" onClick={() => setFolder(entry.path)}>▸ {entry.name}</button>
            : <button className="text-button" onClick={() => onPreview(entry.path)}>{entry.name} <small>{entry.size.toLocaleString()} B</small></button>}
        </li>)}
      </ul>
      {listing?.truncated && <small>Showing the first files only.</small>}
    </div>
  );
}
