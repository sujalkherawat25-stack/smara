/**
 * api/client.ts — Typed HTTP client for all backend API calls.
 *
 * Thin wrapper around the Fetch API. Every request:
 *   • Uses `credentials: "include"` so the httpOnly mem_session cookie
 *     travels with the request. Backend reads cookie → resolves account.
 *   • JSON request/response unless explicitly multipart.
 *   • Throws a typed ApiError on non-2xx responses.
 *
 * Pre-F3 this module sent an X-User-ID header. After F3 the cookie is the
 * single auth carrier — no user_id ever leaves the client by other means.
 */

import { smaraFetch, smaraModeEnabled } from "@/lib/smaraGateway";

const BASE_URL = "";

export class ApiError extends Error {
  constructor(public status: number, public detail: string) {
    super(`API error ${status}: ${detail}`);
  }
}

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
  params?: Record<string, string>,
): Promise<T> {
  const url = new URL(BASE_URL + path, window.location.origin);
  if (params) {
    Object.entries(params).forEach(([k, v]) => url.searchParams.set(k, v));
  }

  const query = url.search ? url.search : "";
  const target = smaraModeEnabled() ? `${path}${query}` : url.toString();
  const res = await smaraFetch(target, {
    method,
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new ApiError(res.status, err.detail ?? res.statusText);
  }

  return res.json() as Promise<T>;
}

export const apiClient = {
  get:    <T>(path: string, params?: Record<string, string>) =>
            request<T>("GET", path, undefined, params),
  post:   <T>(path: string, body?: unknown) => request<T>("POST", path, body),
  patch:  <T>(path: string, body: unknown)  => request<T>("PATCH", path, body),
  put:    <T>(path: string, body: unknown)  => request<T>("PUT", path, body),
  delete: <T>(path: string, body?: unknown) => request<T>("DELETE", path, body),

  uploadFile: async <T>(path: string, file: File): Promise<T> => {
    // multipart/form-data — do NOT set Content-Type; the browser adds the
    // boundary automatically. The session cookie still travels via credentials.
    const form = new FormData();
    form.append("file", file);
    const res = await smaraFetch(BASE_URL + path, {
      method: "POST",
      credentials: "include",
      body: form,
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new ApiError(res.status, err.detail ?? res.statusText);
    }
    return res.json() as Promise<T>;
  },

  streamPost: (path: string, body: unknown): Promise<Response> => {
    // Returns raw Response so caller can consume as ReadableStream (SSE chat).
    return smaraFetch(BASE_URL + path, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  },
};
