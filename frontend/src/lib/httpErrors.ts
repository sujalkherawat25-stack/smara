/**
 * lib/httpErrors.ts — recognise "the server is mid-deploy" failures so the
 * UI can show a reassuring message instead of a raw HTTP/network error.
 *
 * During a `docker compose up --build` redeploy, the backend (and sometimes
 * the frontend nginx container) is briefly unreachable. Depending on which
 * hop fails, the browser sees either a 502/503/504 from nginx/Caddy, or a
 * bare connection-refused (fetch throws a generic TypeError). Both are the
 * same story to the user: "try again in a minute or two," not "something is
 * broken."
 */

const MAINTENANCE_STATUS_PATTERN = /\b(502|503|504)\b/;

export function isMaintenanceStatus(status: number): boolean {
  return status === 502 || status === 503 || status === 504;
}

/** Best-effort check on a caught fetch/stream error (status already lost by this point). */
export function isMaintenanceError(err: unknown): boolean {
  if (!(err instanceof Error)) return false;
  if (MAINTENANCE_STATUS_PATTERN.test(err.message)) return true;
  // Browsers throw a generic TypeError ("Failed to fetch" / "NetworkError
  // when attempting to fetch resource") when the connection is refused
  // outright — e.g. the frontend container itself is mid-restart.
  return err.name === "TypeError" || /failed to fetch|networkerror/i.test(err.message);
}

export const MAINTENANCE_MESSAGE =
  "Smara is being updated right now — please try again in a couple of minutes.";
