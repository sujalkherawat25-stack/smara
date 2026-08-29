import { useEffect, useState } from "react";
import { smaraModeEnabled, smaraUrl } from "@/lib/smaraGateway";
import { ensureNotificationPermission } from "@/stores/notificationsStore";

/**
 * Smara no longer opens the legacy Memento event bus. Chat progress is carried
 * by the Smara chat stream and durable task progress is refreshed by Work /
 * Activity. This hook remains as a small service-health indicator for the
 * shell so existing layout code does not need a second migration.
 */
export function useSSE() {
  const [connected, setConnected] = useState(false);
  useEffect(() => {
    if (!smaraModeEnabled()) return;
    ensureNotificationPermission();
    let cancelled = false;
    const probe = async () => {
      try {
        const response = await fetch(smaraUrl("/health"), { credentials: "include", headers: { Accept: "application/json" } });
        if (!cancelled) setConnected(response.ok);
      } catch {
        if (!cancelled) setConnected(false);
      }
    };
    void probe();
    const timer = window.setInterval(probe, 30_000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, []);
  return { connected };
}
