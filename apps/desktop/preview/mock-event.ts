// Preview only: an in-browser stand-in for @tauri-apps/api/event. Never part of the desktop build.
type Handler = (event: { event: string; payload: unknown }) => void;
const handlers = new Map<string, Set<Handler>>();

export async function listen<T>(name: string, handler: (event: { event: string; payload: T }) => void): Promise<() => void> {
  const set = handlers.get(name) ?? new Set<Handler>();
  set.add(handler as Handler);
  handlers.set(name, set);
  return () => { set.delete(handler as Handler); };
}

export function emit(name: string, payload: unknown): void {
  for (const handler of handlers.get(name) ?? []) handler({ event: name, payload });
}
