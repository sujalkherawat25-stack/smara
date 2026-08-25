/**
 * Settings/panels/RemindersPanel.tsx — upcoming reminders + cancel.
 *
 * Reminders are created via chat (the agent's set_reminder tool). This panel
 * is read+delete only. Re-ticks every 30s so "in 5 min" updates live.
 */

import { useState, useEffect } from "react";
import { Trash2, Bell } from "lucide-react";
import { useRemindersStore } from "@/stores/remindersStore";

export default function RemindersPanel() {
  const items = useRemindersStore((s) => s.items);
  const loaded = useRemindersStore((s) => s.loaded);
  const refresh = useRemindersStore((s) => s.refresh);
  const cancel = useRemindersStore((s) => s.cancel);

  const [confirmId, setConfirmId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  // Re-render every 30s so relative time strings stay fresh.
  const [, setTick] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setTick((n) => n + 1), 30_000);
    return () => clearInterval(t);
  }, []);

  const handleCancel = async (id: string) => {
    if (confirmId !== id) { setConfirmId(id); return; }
    setBusyId(id);
    setError(null);
    try {
      const ok = await cancel(id);
      if (!ok) setError("Couldn't cancel that reminder. Refresh and try again.");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't cancel that reminder.");
    } finally {
      setBusyId(null);
      setConfirmId(null);
    }
  };

  if (!loaded) {
    return <p className="text-[12px]" style={{ color: "var(--text-muted)" }}>Loading reminders…</p>;
  }

  return (
    <div className="flex flex-col gap-3 max-w-2xl">
      <div className="flex items-center justify-between">
        <p className="text-[13px]" style={{ color: "var(--text-secondary)" }}>
          {items.length === 0
            ? "No reminders set."
            : `${items.length} upcoming reminder${items.length === 1 ? "" : "s"}`}
        </p>
        <button
          onClick={refresh}
          className="text-[11px] px-2 py-1 rounded-md"
          style={{ color: "var(--text-muted)", border: "1px solid var(--border-dim)" }}
        >
          Refresh
        </button>
      </div>

      {items.length === 0 && (
        <div
          className="px-4 py-5 rounded-xl text-center"
          style={{ background: "var(--bg-card)", border: "1px dashed var(--border-default)" }}
        >
          <p className="text-[12px]" style={{ color: "var(--text-secondary)" }}>
            Ask the agent — e.g.
          </p>
          <p className="text-[12px] mt-1 font-mono" style={{ color: "var(--text-primary)" }}>
            "remind me to call Riya at 6pm"
          </p>
        </div>
      )}

      {items.length > 0 && (
        <div className="flex flex-col gap-2 max-h-[480px] overflow-y-auto pr-1">
          {items.map((rem) => {
            const fireDate = new Date(rem.fire_at);
            const ms = fireDate.getTime() - Date.now();
            const isPast = ms <= 0;
            const absolute = fireDate.toLocaleString(undefined, {
              dateStyle: "medium",
              timeStyle: "short",
            });
            return (
              <div
                key={rem.id}
                className="px-3 py-2.5 rounded-xl flex items-start gap-3"
                style={{ background: "var(--bg-card)", border: "1px solid var(--border-dim)" }}
              >
                <div
                  className="w-7 h-7 rounded-md flex items-center justify-center shrink-0 mt-0.5"
                  style={{ background: "var(--accent-soft)" }}
                >
                  <Bell size={12} style={{ color: "var(--accent)" }} />
                </div>
                <div className="flex-1 min-w-0">
                  <p
                    className="text-[13px] leading-snug truncate"
                    style={{ color: "var(--text-primary)" }}
                    title={rem.message}
                  >
                    {rem.message}
                  </p>
                  <p className="text-[11px] mt-1" style={{ color: "var(--text-muted)" }}>
                    {isPast ? "firing now…" : formatRelative(ms)} · {absolute}
                  </p>
                </div>
                <button
                  onClick={() => handleCancel(rem.id)}
                  disabled={busyId === rem.id}
                  className="shrink-0 px-2 py-1.5 rounded-md text-[11px] transition-all"
                  style={{
                    background: confirmId === rem.id ? "rgba(239,68,68,0.10)" : "transparent",
                    color: confirmId === rem.id ? "#ef4444" : "var(--text-muted)",
                    border: `1px solid ${confirmId === rem.id ? "rgba(239,68,68,0.30)" : "var(--border-dim)"}`,
                  }}
                  title={confirmId === rem.id ? "Click again to confirm" : "Cancel reminder"}
                >
                  {busyId === rem.id ? "…" : confirmId === rem.id ? "Confirm?" : <Trash2 size={12} />}
                </button>
              </div>
            );
          })}
        </div>
      )}

      {error && <p className="text-[11px]" style={{ color: "#ef4444" }}>{error}</p>}
    </div>
  );
}

function formatRelative(msFromNow: number): string {
  if (msFromNow <= 0) return "now";
  const s = Math.floor(msFromNow / 1000);
  if (s < 60) return `in ${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `in ${m} min`;
  const h = Math.floor(m / 60);
  if (h < 24) {
    const remM = m % 60;
    return remM ? `in ${h}h ${remM}m` : `in ${h}h`;
  }
  const d = Math.floor(h / 24);
  return d === 1 ? "in 1 day" : `in ${d} days`;
}
