import { useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import type { MemoryStatus, NativeSetup } from "./nativeApi";

type Health = { search: NativeSetup["search"]; memory: MemoryStatus; network_probes: number };
function readHealth(value: unknown): Health {
  if (!value || typeof value !== "object") throw Error("Invalid local health response");
  const next = value as Health, memory = next.memory;
  if (!next.search || typeof next.search.configured !== "boolean" || next.network_probes !== 0
      || !memory || typeof memory.enabled !== "boolean" || typeof memory.detail !== "string"
      || !memory.writes || typeof memory.writes !== "object" || Array.isArray(memory.writes)
      || Object.values(memory.writes).some(count => !Number.isSafeInteger(count) || count < 0)
      || memory.level !== undefined && !["disabled", "unconfigured", "ready", "pending", "warning"].includes(memory.level)
      || memory.last_activity != null && typeof memory.last_activity.detail !== "string") {
    throw Error("Invalid local health response");
  }
  return next;
}
export default function NativeHealth({ workspace, locked, onSettings }: { workspace: string; locked: boolean; onSettings: (section: "memory" | "tools") => void }) {
  const [value, setValue] = useState<Health | null>(null), [error, setError] = useState("");
  useEffect(() => {
    let disposed = false, timer: ReturnType<typeof setTimeout> | undefined;
    setValue(null); setError("");
    const poll = async () => {
      if (!workspace || disposed) return;
      try {
        const next = readHealth(await invoke<unknown>("native_manage", { request: { operation: "runtime_health", workspace } }));
        if (!disposed) { setValue(next); setError(""); }
      } catch { if (!disposed) setError("Connection status unavailable; this is not proof that a provider is down."); }
      finally { if (!disposed) timer = setTimeout(() => void poll(), 30000); }
    };
    void poll();
    return () => { disposed = true; if (timer) clearTimeout(timer); };
  }, [workspace]);
  const memory = value?.memory, failed = memory?.writes.failed || 0;
  const pending = (memory?.writes.pending || 0) + (memory?.writes.submitted || 0);
  const warning = error || memory?.enabled && (memory.level === "warning" || failed > 0);
  return <details className={`chat-health ${warning ? "has-warning" : ""}`}>
    <summary>{warning ? "Connections · needs attention" : pending ? `Memory sync · ${pending} pending` : "Connections & memory"}</summary>
    {error && <p role="status">{error}</p>}
    {value && <><p>Search: {value.search.configured ? "Saved key readable" : "Not configured"}. Not a live search test.</p>
      <button disabled={locked} onClick={() => onSettings("tools")}>Search settings</button>
      <p role={memory?.enabled && memory.level === "warning" ? "status" : undefined}>Memory: {memory?.enabled ? `${failed} failed · ${pending} pending` : "Off"}. {memory?.detail}</p>
      {memory?.enabled && memory.last_activity && <small>{memory.last_activity.detail}</small>}
      <small>Native history is separate from remote memory. Local status inspection sends no health probes or private history.</small>
      <button disabled={locked} onClick={() => onSettings("memory")}>Memory settings</button></>}
  </details>;
}
