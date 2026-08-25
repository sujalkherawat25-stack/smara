/**
 * stores/projectsStore.ts — F8 Projects & documents client state.
 *
 * currentProjectId is threaded into chat requests (chatStore.send) so the
 * agent's search_documents tool scopes to the open project. Selecting a
 * project also loads its documents for the DocumentsPanel.
 */

import { create } from "zustand";
import {
  fetchProjects,
  createProject as apiCreate,
  updateProject as apiUpdate,
  deleteProject as apiDeleteProject,
  fetchProjectDetail,
  uploadProjectDocument,
  setDocumentActive as apiSetActive,
  deleteProjectDocument as apiDeleteDoc,
  ProjectsNotAvailable,
  type Project,
  type ProjectDocument,
} from "@/lib/projects";

interface ProjectsState {
  items: Project[];
  loaded: boolean;
  available: boolean;          // false once a 404 (plan-gated) is seen
  error: string | null;        // last load/mutate error, surfaced in the panel
  currentProjectId: string | null;
  documents: ProjectDocument[];
  documentsLoading: boolean;

  refresh: () => Promise<void>;
  create: (name: string, instructions?: string) => Promise<Project>;
  updateInstructions: (projectId: string, instructions: string) => Promise<void>;
  remove: (projectId: string) => Promise<boolean>;
  open: (projectId: string) => Promise<void>;
  closeProject: () => void;
  upload: (file: File) => Promise<void>;
  toggleActive: (docId: string, active: boolean) => Promise<void>;
  removeDocument: (docId: string) => Promise<void>;
  refreshDocuments: () => Promise<void>;
}

export const useProjectsStore = create<ProjectsState>((set, get) => ({
  items: [],
  loaded: false,
  available: true,
  error: null,
  currentProjectId: null,
  documents: [],
  documentsLoading: false,

  refresh: async () => {
    try {
      const items = await fetchProjects();
      set({ items, loaded: true, available: true, error: null });
    } catch (e) {
      if (e instanceof ProjectsNotAvailable) {
        set({ items: [], loaded: true, available: false, error: null });
        return;
      }
      // Don't hide a real failure behind an empty "No projects yet" state —
      // surface it so the user (and we) see the actual reason.
      console.error("[projects] refresh failed:", e);
      set({ loaded: true, error: e instanceof Error ? e.message : "Couldn't load projects." });
    }
  },

  // Throws on failure so the caller can show the real error inline instead of
  // silently resetting (which looked identical to "nothing happened").
  create: async (name, instructions) => {
    const project = await apiCreate(name, instructions);
    set((s) => ({ items: [project, ...s.items], error: null }));
    return project;
  },

  updateInstructions: async (projectId, instructions) => {
    const updated = await apiUpdate(projectId, { instructions });
    set((s) => ({
      items: s.items.map((p) => (p.id === projectId ? updated : p)),
    }));
  },

  remove: async (projectId) => {
    const ok = await apiDeleteProject(projectId);
    if (ok) {
      set((s) => ({
        items: s.items.filter((p) => p.id !== projectId),
        currentProjectId: s.currentProjectId === projectId ? null : s.currentProjectId,
        documents: s.currentProjectId === projectId ? [] : s.documents,
      }));
    }
    return ok;
  },

  open: async (projectId) => {
    set({ currentProjectId: projectId, documentsLoading: true });
    try {
      const detail = await fetchProjectDetail(projectId);
      set({ documents: detail.documents, documentsLoading: false });
    } catch (e) {
      console.error("[projects] open failed:", e);
      set({ documentsLoading: false });
    }
  },

  closeProject: () => set({ currentProjectId: null, documents: [] }),

  upload: async (file) => {
    const projectId = get().currentProjectId;
    if (!projectId) return;
    try {
      const doc = await uploadProjectDocument(projectId, file);
      set((s) => ({ documents: [doc, ...s.documents] }));
      // Poll for status='processing' → 'ready'/'failed' so the UI chip
      // updates without requiring a manual refresh. A large PDF genuinely
      // takes 30-60s to ingest (extract + embed 400 chunks) — the old
      // 10×2s poll gave up at 20s and left the chip stuck on "processing"
      // forever even though the backend had finished. Now: 2s cadence for
      // the first 20s, then 5s up to ~3 minutes total.
      const poll = async (attempt: number) => {
        if (attempt > 40) return;
        await new Promise((r) => setTimeout(r, attempt <= 10 ? 2000 : 5000));
        if (get().currentProjectId !== projectId) return;
        await get().refreshDocuments();
        const updated = get().documents.find((d) => d.id === doc.id);
        if (updated && updated.status === "processing") await poll(attempt + 1);
      };
      void poll(1);
    } catch (e) {
      console.error("[projects] upload failed:", e);
      throw e;
    }
  },

  toggleActive: async (docId, active) => {
    const projectId = get().currentProjectId;
    if (!projectId) return;
    // Optimistic update.
    set((s) => ({
      documents: s.documents.map((d) => (d.id === docId ? { ...d, active } : d)),
    }));
    try {
      await apiSetActive(projectId, docId, active);
    } catch (e) {
      console.error("[projects] toggleActive failed:", e);
      await get().refreshDocuments();
    }
  },

  removeDocument: async (docId) => {
    const projectId = get().currentProjectId;
    if (!projectId) return;
    const ok = await apiDeleteDoc(projectId, docId);
    if (ok) {
      set((s) => ({ documents: s.documents.filter((d) => d.id !== docId) }));
    }
  },

  refreshDocuments: async () => {
    const projectId = get().currentProjectId;
    if (!projectId) return;
    try {
      const detail = await fetchProjectDetail(projectId);
      set({ documents: detail.documents });
    } catch (e) {
      console.error("[projects] refreshDocuments failed:", e);
    }
  },
}));
