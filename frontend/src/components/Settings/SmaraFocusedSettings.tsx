import { useCallback, useEffect, useState } from "react";
import { CheckCircle2, CircleAlert, Cpu, Laptop, LogOut, RefreshCw, Server } from "lucide-react";
import { apiClient, ApiError } from "@/api/client";
import { useAuthStore } from "@/stores/authStore";
import { useThemeStore } from "@/stores/themeStore";
import { useChatStore } from "@/stores/chatStore";

interface Executor {
  id: string;
  name: string;
  status: string;
  capabilities: string[];
  last_seen_at?: string | null;
}

interface ToolCatalogue {
  tools?: Array<{ name: string }>;
}
interface HostedModel {
  name: string;
  model: string;
  capability: string;
  configured: boolean;
  default?: boolean;
}

/**
 * Settings for the focused Smara product. It intentionally uses only Smara
 * endpoints: account, hosted tool catalogue, and paired desktop executors.
 * Deferred Telegram/Google/legacy Memento panels are not mounted here.
 */
export default function SmaraFocusedSettings({ onOpenWork }: { onOpenWork?: () => void }) {
  const account = useAuthStore((s) => s.account);
  const signOut = useAuthStore((s) => s.signOut);
  const { theme, toggle } = useThemeStore();
  const modelProfile = useChatStore((s) => s.modelProfile);
  const setModelProfile = useChatStore((s) => s.setModelProfile);
  const [executors, setExecutors] = useState<Executor[]>([]);
  const [toolCount, setToolCount] = useState<number | null>(null);
  const [models, setModels] = useState<HostedModel[]>([]);
  const [loading, setLoading] = useState(true);
  const [runtimeStatus, setRuntimeStatus] = useState<"checking" | "connected" | "unavailable">("checking");
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [executorData, tools, modelData] = await Promise.all([
        apiClient.get<{ executors?: Executor[] }>("/v1/executors"),
        apiClient.get<ToolCatalogue>("/v1/tools"),
        apiClient.get<{ models?: HostedModel[] }>("/v1/models"),
      ]);
      setExecutors(executorData.executors ?? []);
      setToolCount(tools.tools?.length ?? 0);
      setModels(modelData.models ?? []);
      setRuntimeStatus("connected");
    } catch (cause) {
      setRuntimeStatus("unavailable");
      setError(cause instanceof ApiError ? cause.detail : "Could not load Smara status.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  async function revoke(executorId: string) {
    setBusyId(executorId);
    setError(null);
    try {
      await apiClient.delete<void>(`/v1/executors/${encodeURIComponent(executorId)}`);
      setExecutors((current) => current.filter((item) => item.id !== executorId));
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.detail : "Could not revoke this desktop.");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="flex flex-col gap-5 max-w-2xl">
      <section className="rounded-xl p-4" style={{ background: "var(--bg-card)", border: "1px solid var(--border-default)" }}>
        <div className="flex items-center gap-3">
          <Server size={18} style={{ color: "var(--accent)" }} />
          <div className="min-w-0 flex-1">
            <h2 className="text-[14px] font-semibold" style={{ color: "var(--text-primary)" }}>Hosted Smara</h2>
            <p className="text-[12px] mt-1" style={{ color: "var(--text-muted)" }}>One agent brain for this web app, the CLI, and durable work.</p>
          </div>
          <RuntimeBadge status={runtimeStatus} />
        </div>
        <div className="grid grid-cols-2 gap-2 mt-4">
          <StatusStat label="Account" value={account?.account_id || "—"} />
          <StatusStat label="Available tools" value={toolCount == null ? "—" : String(toolCount)} />
        </div>
      </section>

      <section className="rounded-xl p-4" style={{ background: "var(--bg-card)", border: "1px solid var(--border-default)" }}>
        <div className="flex items-center gap-3">
          <Cpu size={18} style={{ color: "var(--accent2)" }} />
          <div className="min-w-0 flex-1">
            <h2 className="text-[14px] font-semibold" style={{ color: "var(--text-primary)" }}>Hosted model</h2>
            <p className="text-[12px] mt-1" style={{ color: "var(--text-muted)" }}>Choose the server-side model. Your API keys never leave Smara.</p>
          </div>
        </div>
        <select
          value={modelProfile ?? ""}
          onChange={(event) => setModelProfile(event.target.value || null)}
          className="mt-3 w-full rounded-lg px-3 py-2 text-[12px] outline-none"
          style={{ background: "var(--bg-surface)", border: "1px solid var(--border-default)", color: "var(--text-primary)" }}
        >
          <option value="">Automatic (recommended)</option>
          {models.map((model) => (
            <option key={model.name} value={model.name} disabled={!model.configured}>
              {model.name} · {model.model}{model.configured ? "" : " · unavailable"}
            </option>
          ))}
        </select>
        {models.length > 0 && <p className="text-[11px] mt-2" style={{ color: "var(--text-muted)" }}>
          {models.filter((model) => model.configured).length} model{models.filter((model) => model.configured).length === 1 ? "" : "s"} ready · Sarvam beta models appear only when your key has access.
        </p>}
      </section>

      <section className="rounded-xl p-4" style={{ background: "var(--bg-card)", border: "1px solid var(--border-default)" }}>
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <Laptop size={18} style={{ color: "var(--accent2)" }} />
            <div>
              <h2 className="text-[14px] font-semibold" style={{ color: "var(--text-primary)" }}>Desktop executors</h2>
              <p className="text-[12px] mt-1" style={{ color: "var(--text-muted)" }}>Paired PCs can act only after you approve a task.</p>
            </div>
          </div>
          <button onClick={() => void refresh()} disabled={loading} className="p-2 rounded-md" style={{ border: "1px solid var(--border-dim)", color: "var(--text-muted)" }} title="Refresh status"><RefreshCw size={14} className={loading ? "animate-spin" : ""} /></button>
        </div>
        <div className="mt-3 flex flex-col gap-2">
          {!loading && executors.length === 0 && <p className="text-[12px]" style={{ color: "var(--text-muted)" }}>No desktop is paired yet. Pair one from the Smara Desktop app.</p>}
          {executors.map((executor) => (
            <div key={executor.id} className="flex items-center gap-3 rounded-lg px-3 py-2.5" style={{ background: "var(--bg-surface)", border: "1px solid var(--border-dim)" }}>
              <span className="w-2 h-2 rounded-full" style={{ background: executor.status === "active" ? "#34d399" : "var(--text-muted)" }} />
              <div className="min-w-0 flex-1"><p className="text-[12px] truncate" style={{ color: "var(--text-primary)" }}>{executor.name}</p><p className="text-[10px] truncate" style={{ color: "var(--text-muted)" }}>{executor.capabilities.join(" · ") || "No capabilities declared"}</p></div>
              <button onClick={() => void revoke(executor.id)} disabled={busyId === executor.id} className="text-[11px] px-2 py-1 rounded-md" style={{ color: "#fca5a5", border: "1px solid rgba(248,113,113,.35)", opacity: busyId === executor.id ? 0.6 : 1 }}>Revoke</button>
            </div>
          ))}
        </div>
        {onOpenWork && <button onClick={onOpenWork} className="mt-3 px-3 py-2 rounded-md text-[12px] font-medium" style={{ background: "var(--accent-soft)", color: "var(--accent)" }}>Open durable work</button>}
      </section>

      <section className="rounded-xl p-4" style={{ background: "var(--bg-card)", border: "1px solid var(--border-default)" }}>
        <h2 className="text-[14px] font-semibold" style={{ color: "var(--text-primary)" }}>Account</h2>
        <p className="text-[12px] mt-1" style={{ color: "var(--text-muted)" }}>{account?.display_name || account?.email || "Signed-in Smara account"}</p>
        <div className="flex flex-wrap items-center gap-2 mt-4">
          <button onClick={toggle} className="px-3 py-2 rounded-md text-[12px]" style={{ border: "1px solid var(--border-dim)", color: "var(--text-secondary)" }}>Use {theme === "dark" ? "light" : "dark"} theme</button>
          <button onClick={() => void signOut()} className="flex items-center gap-1.5 px-3 py-2 rounded-md text-[12px]" style={{ border: "1px solid rgba(248,113,113,.35)", color: "#fca5a5" }}><LogOut size={13} /> Sign out</button>
        </div>
      </section>

      {error && <p className="flex items-center gap-1.5 text-[12px]" style={{ color: "#f87171" }}><CircleAlert size={14} />{error}</p>}
    </div>
  );
}

function RuntimeBadge({ status }: { status: "checking" | "connected" | "unavailable" }) {
  if (status === "checking") {
    return <span className="flex items-center gap-1 text-[11px]" style={{ color: "#fbbf24" }}><RefreshCw size={13} className="animate-spin" /> Checking…</span>;
  }
  if (status === "unavailable") {
    return <span className="flex items-center gap-1 text-[11px]" style={{ color: "#f87171" }}><CircleAlert size={13} /> Unavailable</span>;
  }
  return <span className="flex items-center gap-1 text-[11px]" style={{ color: "#34d399" }}><CheckCircle2 size={13} /> Connected</span>;
}

function StatusStat({ label, value }: { label: string; value: string }) {
  return <div className="rounded-lg px-3 py-2" style={{ background: "var(--bg-surface)" }}><p className="text-[10px] uppercase tracking-wider" style={{ color: "var(--text-muted)" }}>{label}</p><p className="text-[12px] mt-1 truncate" style={{ color: "var(--text-secondary)" }}>{value}</p></div>;
}
