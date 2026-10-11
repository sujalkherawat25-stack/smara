import { useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";

type Preview = {
  file_name: string;
  relative_path: string;
  size_bytes: number;
  extension: string;
  kind: "text" | "pdf" | "image" | "unsupported" | "too_large";
  content: string | null;
  data_url: string | null;
  note: string | null;
  can_open_externally: boolean;
};

export default function NativeFilePreview({ workspace, path, onClose }: {
  workspace: string;
  path: string;
  onClose: () => void;
}) {
  const [preview, setPreview] = useState<Preview | null>(null);
  const [error, setError] = useState("");
  const [opening, setOpening] = useState(false);

  useEffect(() => {
    let current = true;
    setPreview(null); setError("");
    void invoke<Preview>("native_preview_file", { workspace, path })
      .then(value => { if (current) setPreview(value); })
      .catch(reason => { if (current) setError(String(reason)); });
    return () => { current = false; };
  }, [workspace, path]);

  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [onClose]);

  const openExternally = async () => {
    setOpening(true); setError("");
    try { await invoke("native_open_workspace_file", { workspace, path }); }
    catch (reason) { setError(String(reason)); }
    finally { setOpening(false); }
  };

  return <div className="native-preview-backdrop" onMouseDown={event => { if (event.target === event.currentTarget) onClose(); }}>
    <section className="native-preview-dialog" role="dialog" aria-modal="true" aria-labelledby="native-preview-title" aria-busy={!preview && !error}>
      <header className="native-preview-header">
        <div><strong id="native-preview-title">{preview?.file_name ?? "File preview"}</strong><small>{preview?.relative_path ?? path}{preview && ` · ${(preview.size_bytes / 1024).toFixed(1)} KB`}</small></div>
        <div className="native-preview-actions"><button type="button" disabled={opening || !preview?.can_open_externally} onClick={() => void openExternally()}>{opening ? "Opening…" : "Open externally"}</button><button type="button" aria-label="Close file preview" onClick={onClose}>×</button></div>
      </header>
      {error ? <p className="native-preview-message" role="alert">Could not preview this project file: {error}</p>
        : !preview ? <p className="native-preview-message" role="status">Loading a read-only preview…</p>
          : preview.kind === "text" ? <pre className="native-preview-text">{preview.content}</pre>
            : preview.kind === "image" && preview.data_url ? <div className="native-preview-image-wrap"><img src={preview.data_url} alt={preview.file_name} /></div>
              : preview.kind === "pdf" && preview.data_url ? <iframe className="native-preview-pdf" title={`Preview of ${preview.file_name}`} src={preview.data_url} />
                : <p className="native-preview-message">{preview.note ?? "This file cannot be previewed inline."} Use “Open externally” to view it with an installed app.</p>}
    </section>
  </div>;
}
