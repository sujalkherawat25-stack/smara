import { create } from "zustand";

/**
 * pdfPreviewStore — drives the right-side PDF preview panel (App.tsx).
 *
 * Separate from viewStore's graph/memory/settings panels on purpose: those
 * dim + overlay the chat (PanelView); the PDF panel pushes the chat aside
 * and stays open alongside it, Claude-Code-style, so the user can keep
 * chatting while the document stays visible.
 */
interface PdfPreviewStore {
  open: boolean;
  url: string | null;
  filename: string | null;
  show: (url: string, filename: string) => void;
  close: () => void;
}

export const usePdfPreviewStore = create<PdfPreviewStore>((set) => ({
  open: false,
  url: null,
  filename: null,
  show: (url, filename) => set({ open: true, url, filename }),
  close: () => set({ open: false, url: null, filename: null }),
}));
