/**
 * lib/projects.ts — frontend API helpers for F8 Projects & documents.
 *
 * Mirrors lib/reminders.ts: cookie auth via credentials: "include", plain
 * fetch (not apiClient) so a 404 (feature not released to this plan) can
 * be handled gracefully instead of throwing.
 */

export interface Project {
  id: string;
  name: string;
  instructions: string | null;
  created_at: string;
  updated_at: string;
}

export interface ProjectDocument {
  id: string;
  project_id: string;
  name: string;
  mime: string | null;
  size_bytes: number;
  chunk_count: number;
  active: boolean;
  status: "processing" | "ready" | "failed";
  error: string | null;
  created_at: string;
}

const COMMON: RequestInit = {
  credentials: "include",
  headers: { "Content-Type": "application/json" },
};

/** True when the caller's plan doesn't have Projects released yet. */
export class ProjectsNotAvailable extends Error {}

async function handle<T>(r: Response): Promise<T> {
  if (r.status === 404) {
    throw new ProjectsNotAvailable("Projects isn't available on your plan yet.");
  }
  if (!r.ok) {
    const detail = await r.json().catch(() => ({ detail: r.statusText }));
    throw new Error(detail.detail || `HTTP ${r.status}`);
  }
  return (await r.json()) as T;
}

export async function fetchProjects(): Promise<Project[]> {
  const r = await fetch("/v1/memento/projects", { ...COMMON, method: "GET" });
  return handle<Project[]>(r);
}

export async function createProject(name: string, instructions?: string): Promise<Project> {
  const r = await fetch("/v1/memento/projects", {
    ...COMMON, method: "POST",
    body: JSON.stringify({ name, instructions: instructions || undefined }),
  });
  return handle<Project>(r);
}

export async function updateProject(
  projectId: string, patch: { name?: string; instructions?: string },
): Promise<Project> {
  const r = await fetch(`/v1/memento/projects/${encodeURIComponent(projectId)}`, {
    ...COMMON, method: "PATCH", body: JSON.stringify(patch),
  });
  return handle<Project>(r);
}

export async function deleteProject(projectId: string): Promise<boolean> {
  const r = await fetch(`/v1/memento/projects/${encodeURIComponent(projectId)}`, {
    ...COMMON, method: "DELETE",
  });
  return r.ok;
}

export async function fetchProjectDetail(
  projectId: string,
): Promise<{ project: Project; documents: ProjectDocument[] }> {
  const r = await fetch(`/v1/memento/projects/${encodeURIComponent(projectId)}`, {
    ...COMMON, method: "GET",
  });
  return handle(r);
}

export async function uploadProjectDocument(
  projectId: string, file: File,
): Promise<ProjectDocument> {
  const form = new FormData();
  form.append("file", file);
  const r = await fetch(`/v1/memento/projects/${encodeURIComponent(projectId)}/documents`, {
    method: "POST", credentials: "include", body: form,
  });
  return handle<ProjectDocument>(r);
}

export async function listProjectDocuments(projectId: string): Promise<ProjectDocument[]> {
  const r = await fetch(`/v1/memento/projects/${encodeURIComponent(projectId)}/documents`, {
    ...COMMON, method: "GET",
  });
  return handle<ProjectDocument[]>(r);
}

export async function setDocumentActive(
  projectId: string, docId: string, active: boolean,
): Promise<ProjectDocument> {
  const r = await fetch(
    `/v1/memento/projects/${encodeURIComponent(projectId)}/documents/${encodeURIComponent(docId)}`,
    { ...COMMON, method: "PATCH", body: JSON.stringify({ active }) },
  );
  return handle<ProjectDocument>(r);
}

export async function deleteProjectDocument(projectId: string, docId: string): Promise<boolean> {
  const r = await fetch(
    `/v1/memento/projects/${encodeURIComponent(projectId)}/documents/${encodeURIComponent(docId)}`,
    { ...COMMON, method: "DELETE" },
  );
  return r.ok;
}
