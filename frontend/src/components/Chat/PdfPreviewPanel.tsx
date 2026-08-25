import { Download, FileText, X } from "lucide-react";
import { usePdfPreviewStore } from "@/stores/pdfPreviewStore";

/**
 * PdfPreviewPanel — persistent right-side PDF preview (Claude-Code-style
 * side panel, not an overlay). Renders via the browser's native PDF viewer
 * in an <iframe> — no client-side PDF-rendering library needed, and it
 * guarantees correct rendering (fonts, tables, pagination) since it's the
 * same engine the browser uses for any other PDF link.
 *
 * Pushes the chat column aside on desktop (App.tsx sizes it as a fixed-width
 * flex sibling); slides over with a backdrop on mobile, matching the
 * existing Recents-sidebar pattern.
 */
export default function PdfPreviewPanel() {
  const openState = usePdfPreviewStore((s) => s.open);
  const url = usePdfPreviewStore((s) => s.url);
  const filename = usePdfPreviewStore((s) => s.filename);
  const close = usePdfPreviewStore((s) => s.close);

  if (!openState || !url) return null;

  return (
    <>
      {/* Mobile backdrop — desktop pushes content instead, no dimming needed */}
      <div
        onClick={close}
        className="md:hidden fixed inset-0 z-40 animate-fade-in"
        style={{ background: "rgba(0,0,0,0.45)", backdropFilter: "blur(2px)" }}
      />
      <div
        className="fixed md:relative inset-y-0 right-0 z-50 md:z-auto flex flex-col shrink-0 animate-fade-in"
        style={{
          width: "min(92vw, 480px)",
          borderLeft: "1px solid var(--border-dim)",
          background: "var(--bg-base)",
          boxShadow: "var(--shadow-lg)",
        }}
      >
        {/* Header */}
        <div
          className="flex items-center justify-between gap-2 px-4 py-3 shrink-0"
          style={{ borderBottom: "1px solid var(--border-dim)" }}
        >
          <div className="flex items-center gap-2 min-w-0">
            <FileText size={14} style={{ color: "var(--accent)" }} className="shrink-0" />
            <span
              className="text-[13px] font-medium truncate"
              style={{ color: "var(--text-primary)" }}
              title={filename ?? undefined}
            >
              {filename}
            </span>
          </div>
          <div className="flex items-center gap-1 shrink-0">
            <a
              href={`${url}${url.includes("?") ? "&" : "?"}dl=1`}
              download={filename ?? undefined}
              className="p-1.5 rounded-md transition-colors"
              style={{ color: "var(--text-muted)" }}
              title="Download"
              aria-label="Download"
            >
              <Download size={15} />
            </a>
            <button
              onClick={close}
              className="p-1.5 rounded-md transition-colors"
              style={{ color: "var(--text-muted)" }}
              title="Close preview"
              aria-label="Close preview"
            >
              <X size={15} />
            </button>
          </div>
        </div>

        {/* PDF — native browser viewer, always renders correctly */}
        <div className="flex-1 min-h-0" style={{ background: "#525659" }}>
          <iframe
            src={url}
            title={filename ?? "PDF preview"}
            className="w-full h-full border-0"
          />
        </div>
      </div>
    </>
  );
}
