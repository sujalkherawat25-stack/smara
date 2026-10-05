import type { ChatEvent } from "./types";

export function matchesChatRequest(event: ChatEvent, sessionId: string, requestId: string): boolean {
  return (!event.session_id || event.session_id === sessionId)
    && (!event.request_id || event.request_id === requestId);
}

export function elapsedLabel(milliseconds: number): string {
  const seconds = Math.max(0, Math.floor(milliseconds / 1000));
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

export function runIsQuiet(now: number, lastProgressAt: number, title: string): boolean {
  return now - lastProgressAt >= 30_000 && !/waiting for your approval/i.test(title);
}
