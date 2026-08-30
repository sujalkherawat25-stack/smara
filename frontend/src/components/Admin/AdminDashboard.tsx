import { useEffect, useMemo, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Database,
  ExternalLink,
  LayoutDashboard,
  LogOut,
  RefreshCw,
  ShieldCheck,
  Users,
  Workflow,
  XCircle,
} from "lucide-react";
import SmaraLogo from "@/components/SmaraLogo";
import { ApiError, apiClient } from "@/api/client";

type Tab = "overview" | "smara" | "syntarus" | "people";
type AnyRecord = Record<string, unknown>;

type Snapshot = {
  generated_at: string;
  smara: AnyRecord;
  syntarus: AnyRecord;
};

function numberValue(value: unknown): string {
  const n = Number(value ?? 0);
  return Number.isFinite(n) ? n.toLocaleString("en-IN") : "0";
}

function textValue(value: unknown, fallback = "—"): string {
  return typeof value === "string" && value.trim() ? value : fallback;
}

function dateValue(value: unknown): string {
  if (!value) return "—";
  const date = new Date(String(value));
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" });
}

function ago(value: unknown): string {
  if (!value) return "Never";
  const timestamp = Date.parse(String(value));
  if (!Number.isFinite(timestamp)) return "Never";
  const minutes = Math.max(0, Math.floor((Date.now() - timestamp) / 60_000));
  if (minutes < 2) return "Just now";
  if (minutes < 60) return `${minutes}m ago`;
  if (minutes < 1_440) return `${Math.floor(minutes / 60)}h ago`;
  return `${Math.floor(minutes / 1_440)}d ago`;
}

function safeList(value: unknown): AnyRecord[] {
  return Array.isArray(value) ? value.filter((item): item is AnyRecord => Boolean(item && typeof item === "object")) : [];
}

function statusTone(status: string): { color: string; icon: JSX.Element } {
  if (["healthy", "active", "completed"].includes(status)) return { color: "var(--accent2)", icon: <CheckCircle2 size={14} /> };
  if (["failed", "unreachable", "error"].includes(status)) return { color: "#fb7185", icon: <XCircle size={14} /> };
  return { color: "var(--accent)", icon: <AlertTriangle size={14} /> };
}

function Metric({ icon, label, value, hint }: { icon: JSX.Element; label: string; value: string; hint: string }) {
  return (
    <article className="rounded-2xl p-4" style={{ background: "var(--bg-card)", border: "1px solid var(--border-default)" }}>
      <div className="flex items-center justify-between gap-3">
        <span className="grid h-8 w-8 place-items-center rounded-xl" style={{ color: "var(--accent)", background: "var(--accent-soft)" }}>{icon}</span>
        <span className="text-[10px] uppercase tracking-[0.14em]" style={{ color: "var(--text-muted)" }}>{label}</span>
      </div>
      <div className="mt-4 text-2xl font-extrabold tracking-tight" style={{ color: "var(--text-primary)" }}>{value}</div>
      <div className="mt-1 text-xs" style={{ color: "var(--text-muted)" }}>{hint}</div>
    </article>
  );
}

function Boundary({ children, tone = "gold" }: { children: ReactNode; tone?: "gold" | "green" | "blue" }) {
  const color = tone === "green" ? "var(--accent2)" : tone === "blue" ? "#93a9ff" : "var(--accent)";
  return <span className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.12em]" style={{ color, border: `1px solid color-mix(in srgb, ${color} 30%, transparent)`, background: `color-mix(in srgb, ${color} 8%, transparent)` }}><ShieldCheck size={12} />{children}</span>;
}

export default function AdminDashboard() {
  const [tab, setTab] = useState<Tab>(() => {
    const value = new URLSearchParams(window.location.search).get("tab");
    return value === "smara" || value === "syntarus" || value === "people" ? value : "overview";
  });
  const [configured, setConfigured] = useState<boolean | null>(null);
  const [authenticated, setAuthenticated] = useState(false);
  const [secret, setSecret] = useState("");
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [busy, setBusy] = useState(true);
  const [signingIn, setSigningIn] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const smara = snapshot?.smara ?? {};
  const syntarus = snapshot?.syntarus ?? {};
  const tasks = (smara.tasks ?? {}) as AnyRecord;
  const accounts = (smara.accounts ?? {}) as AnyRecord;
  const executors = (smara.executors ?? {}) as AnyRecord;
  const recentTasks = safeList(smara.recent_tasks);
  const people = safeList(accounts.people);
  const integrations = safeList((smara.integrations as AnyRecord | undefined)?.by_provider);
  const eventRows = safeList((smara.events as AnyRecord | undefined)?.recent);

  async function loadSession() {
    try {
      const result = await apiClient.get<{ configured: boolean; authenticated: boolean }>("/v1/admin/session");
      setConfigured(result.configured);
      setAuthenticated(result.authenticated);
      if (result.authenticated) await loadOverview();
      else setBusy(false);
    } catch (err) {
      setBusy(false);
      setError(err instanceof Error ? err.message : "Could not reach the operator console.");
    }
  }

  async function loadOverview() {
    setBusy(true);
    setError(null);
    try {
      const result = await apiClient.get<Snapshot>("/v1/admin/overview");
      setSnapshot(result);
      setAuthenticated(true);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) setAuthenticated(false);
      setError(err instanceof Error ? err.message : "The dashboard could not be refreshed.");
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => { void loadSession(); }, []);

  useEffect(() => {
    if (!authenticated) return undefined;
    const timer = window.setInterval(() => { void loadOverview(); }, 30_000);
    return () => window.clearInterval(timer);
  }, [authenticated]);

  function selectTab(next: Tab) {
    setTab(next);
    const query = next === "overview" ? "" : `?tab=${next}`;
    window.history.replaceState({}, "", `${window.location.pathname}${query}`);
  }

  async function signIn(event: FormEvent) {
    event.preventDefault();
    if (!secret.trim() || signingIn) return;
    setSigningIn(true);
    setError(null);
    try {
      await apiClient.post("/v1/admin/session", { secret: secret.trim() });
      setSecret("");
      setNotice("Operator session active. Data is read-only by default.");
      await loadOverview();
    } catch (err) {
      setError(err instanceof ApiError && err.status === 401 ? "That operator secret was not accepted." : (err instanceof Error ? err.message : "Sign-in failed."));
    } finally {
      setSigningIn(false);
    }
  }

  async function signOut() {
    await apiClient.delete("/v1/admin/session").catch(() => undefined);
    setAuthenticated(false);
    setSnapshot(null);
    setNotice(null);
  }

  const healthStatus = textValue(syntarus.status, "unconfigured");
  const health = statusTone(healthStatus);
  const title = useMemo(() => ({ overview: "Operator overview", smara: "Smara control plane", syntarus: "Syntarus context plane", people: "People & access" }[tab]), [tab]);

  if (configured === false) {
    return <ConsoleFrame onSignOut={() => { window.location.assign("/"); }}><EmptyState title="Operator console is not configured" body="Set SMARA_OPERATOR_SECRET on the Smara service, restart it, and open this page again. Regular account sessions cannot access operational data." /></ConsoleFrame>;
  }

  if (!authenticated) {
    return (
      <ConsoleFrame onSignOut={() => { window.location.assign("/"); }}>
        <div className="mx-auto mt-10 w-full max-w-md rounded-3xl p-7 shadow-2xl" style={{ background: "var(--bg-card)", border: "1px solid var(--border-default)" }}>
          <Boundary>Private operator area</Boundary>
          <h1 className="mt-5 text-2xl font-extrabold tracking-tight" style={{ color: "var(--text-primary)" }}>Open the Smara console</h1>
          <p className="mt-2 text-sm leading-6" style={{ color: "var(--text-secondary)" }}>Review service health, task execution, account activity, and the Syntarus boundary from one place. This console never shows provider keys or raw memory by default.</p>
          <form className="mt-6 space-y-3" onSubmit={signIn}>
            <label className="block text-xs font-semibold uppercase tracking-[0.12em]" style={{ color: "var(--text-muted)" }} htmlFor="operator-secret">Operator secret</label>
            <input id="operator-secret" type="password" autoComplete="current-password" value={secret} onChange={(event) => setSecret(event.target.value)} placeholder="Enter the deployment secret" className="w-full rounded-xl px-3 py-3 text-sm outline-none" style={{ color: "var(--text-primary)", background: "var(--bg-base)", border: "1px solid var(--border-default)" }} />
            <button disabled={signingIn || !secret.trim()} type="submit" className="flex w-full items-center justify-center gap-2 rounded-xl px-4 py-3 text-sm font-bold disabled:cursor-not-allowed disabled:opacity-50" style={{ color: "#101018", background: "var(--accent)" }}>{signingIn ? <RefreshCw size={15} className="animate-spin" /> : <ShieldCheck size={15} />}{signingIn ? "Checking…" : "Open console"}</button>
          </form>
          {error && <p className="mt-4 rounded-xl px-3 py-2 text-xs" style={{ color: "#fda4af", background: "rgba(244,63,94,.1)", border: "1px solid rgba(244,63,94,.25)" }}>{error}</p>}
        </div>
      </ConsoleFrame>
    );
  }

  return (
    <ConsoleFrame onSignOut={() => void signOut()}>
      <div className="mx-auto flex w-full max-w-[1480px] flex-col gap-6 px-4 py-6 sm:px-6 lg:px-8">
        <header className="flex flex-col justify-between gap-5 md:flex-row md:items-end">
          <div>
            <div className="flex items-center gap-2"><Boundary tone="green">Operator session</Boundary><span className="text-xs" style={{ color: "var(--text-muted)" }}>Read-only by default · refreshed every 30s</span></div>
            <h1 className="mt-3 text-3xl font-extrabold tracking-tight" style={{ color: "var(--text-primary)" }}>{title}</h1>
            <p className="mt-1 text-sm" style={{ color: "var(--text-secondary)" }}>One console, two independently owned data planes.</p>
          </div>
          <div className="flex items-center gap-2">
            <span className="hidden text-xs sm:inline" style={{ color: "var(--text-muted)" }}>{snapshot ? `Updated ${ago(snapshot.generated_at)}` : "Loading"}</span>
            <button type="button" onClick={() => void loadOverview()} disabled={busy} className="inline-flex items-center gap-2 rounded-xl px-3 py-2 text-xs font-semibold disabled:opacity-50" style={{ color: "var(--text-primary)", background: "var(--bg-card)", border: "1px solid var(--border-default)" }}><RefreshCw size={14} className={busy ? "animate-spin" : ""} />Refresh</button>
            <button type="button" onClick={() => void signOut()} className="inline-flex items-center gap-2 rounded-xl px-3 py-2 text-xs font-semibold" style={{ color: "var(--text-secondary)", background: "transparent", border: "1px solid var(--border-default)" }}><LogOut size={14} />Sign out</button>
          </div>
        </header>

        {notice && <div className="rounded-xl px-4 py-3 text-sm" style={{ color: "var(--accent2)", background: "var(--accent2-soft)", border: "1px solid color-mix(in srgb, var(--accent2) 22%, transparent)" }}>{notice}</div>}
        {error && <div className="flex items-center justify-between gap-3 rounded-xl px-4 py-3 text-sm" style={{ color: "#fda4af", background: "rgba(244,63,94,.08)", border: "1px solid rgba(244,63,94,.22)" }}><span>{error}</span><button type="button" onClick={() => void loadOverview()} className="font-semibold underline">Retry</button></div>}

        <nav className="flex max-w-full gap-1 overflow-x-auto rounded-2xl p-1" style={{ background: "var(--bg-card)", border: "1px solid var(--border-default)" }} aria-label="Operator sections">
          {([ ["overview", "Overview", <LayoutDashboard size={14} />], ["smara", "Smara control", <Workflow size={14} />], ["syntarus", "Syntarus context", <Database size={14} />], ["people", "People & access", <Users size={14} />] ] as [Tab, string, JSX.Element][]).map(([value, label, icon]) => <button key={value} type="button" onClick={() => selectTab(value)} className="inline-flex shrink-0 items-center gap-2 rounded-xl px-3 py-2 text-xs font-semibold" style={{ color: tab === value ? "var(--text-primary)" : "var(--text-muted)", background: tab === value ? "var(--accent-soft)" : "transparent" }}>{icon}{label}</button>)}
        </nav>

        {busy && !snapshot ? <LoadingState /> : tab === "syntarus" ? <SyntarusView syntarus={syntarus} /> : tab === "people" ? <PeopleView people={people} accounts={accounts} /> : <>
          <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <Metric icon={<Activity size={16} />} label="Tasks" value={numberValue(tasks.total)} hint={`${numberValue(tasks.active)} currently active`} />
            <Metric icon={<Users size={16} />} label="Accounts" value={numberValue(accounts.total)} hint="Smara-native account records" />
            <Metric icon={<Workflow size={16} />} label="Executors" value={numberValue(executors.online)} hint={`${numberValue(executors.total)} paired in total`} />
            <Metric icon={<ShieldCheck size={16} />} label="Unresolved" value={numberValue((smara.dead_letters as AnyRecord | undefined)?.unresolved)} hint="Dead letters needing review" />
          </section>
          <section className="grid gap-5 xl:grid-cols-[1.35fr_.65fr]">
            <RecentTasks rows={recentTasks} />
            <div className="space-y-5"><PlaneCard title="Smara control plane" boundary={textValue(smara.boundary)} icon={<Workflow size={18} />} rows={[ ["Conversations", numberValue((smara.conversations as AnyRecord | undefined)?.total)], ["Artifacts", numberValue((smara.artifacts as AnyRecord | undefined)?.total)], ["Events", numberValue((smara.events as AnyRecord | undefined)?.total)], ["Integrations", numberValue((smara.integrations as AnyRecord | undefined)?.total)] ]} />
              <PlaneCard title="Syntarus context plane" boundary={textValue(syntarus.boundary)} icon={<Database size={18} />} rows={[ ["Status", healthStatus], ["SDK configured", syntarus.sdk_configured ? "Yes" : "No"], ["Probe", `${textValue(syntarus.latency_ms, "—")} ms`], ["Raw memory", syntarus.raw_memory_exposed ? "Exposed" : "Never shown"] ]} tone={health.color} />
            </div>
          </section>
          {tab === "overview" && <section className="grid gap-5 xl:grid-cols-[1fr_1fr]"><RecentEvents rows={eventRows} /><IntegrationSummary rows={integrations} /></section>}
          {tab === "smara" && <SmaraDetails smara={smara} />}
        </>}
      </div>
    </ConsoleFrame>
  );
}

function ConsoleFrame({ children, onSignOut }: { children: ReactNode; onSignOut: () => void }) {
  return <div className="min-h-screen" style={{ background: "var(--bg-base)", color: "var(--text-primary)" }}><header className="sticky top-0 z-20 flex items-center justify-between border-b px-4 py-3 sm:px-6" style={{ background: "color-mix(in srgb, var(--bg-base) 92%, transparent)", borderColor: "var(--border-dim)", backdropFilter: "blur(18px)" }}><a href="/" className="flex items-center gap-3" aria-label="Back to Smara"><SmaraLogo size={30} /><span><span className="block text-sm font-extrabold">Smara</span><span className="block text-[10px]" style={{ color: "var(--text-muted)" }}>operator console</span></span></a><button type="button" onClick={onSignOut} className="inline-flex items-center gap-2 rounded-xl px-3 py-2 text-xs" style={{ color: "var(--text-secondary)", border: "1px solid var(--border-default)" }}><ExternalLink size={13} />Open app</button></header>{children}</div>;
}

function LoadingState() { return <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">{[1, 2, 3, 4].map((item) => <div key={item} className="h-32 animate-pulse rounded-2xl" style={{ background: "var(--bg-card)", border: "1px solid var(--border-default)" }} />)}</div>; }

function EmptyState({ title, body }: { title: string; body: string }) { return <div className="mx-auto mt-10 max-w-xl rounded-3xl p-8 text-center" style={{ background: "var(--bg-card)", border: "1px solid var(--border-default)" }}><ShieldCheck className="mx-auto" size={26} style={{ color: "var(--accent)" }} /><h1 className="mt-4 text-xl font-bold">{title}</h1><p className="mt-2 text-sm leading-6" style={{ color: "var(--text-secondary)" }}>{body}</p></div>; }

function PlaneCard({ title, boundary, icon, rows, tone = "var(--accent)" }: { title: string; boundary: string; icon: JSX.Element; rows: [string, string][]; tone?: string }) { return <article className="rounded-2xl p-5" style={{ background: "var(--bg-card)", border: "1px solid var(--border-default)" }}><div className="flex items-start gap-3"><span className="grid h-9 w-9 place-items-center rounded-xl" style={{ color: tone, background: "var(--accent-soft)" }}>{icon}</span><div><h2 className="text-sm font-bold">{title}</h2><p className="mt-1 text-[11px] leading-5" style={{ color: "var(--text-muted)" }}>{boundary}</p></div></div><div className="mt-5 space-y-2">{rows.map(([label, value]) => <div key={label} className="flex items-center justify-between gap-3 border-b pb-2 text-xs last:border-0 last:pb-0" style={{ borderColor: "var(--border-dim)" }}><span style={{ color: "var(--text-muted)" }}>{label}</span><b>{value}</b></div>)}</div></article>; }

function RecentTasks({ rows }: { rows: AnyRecord[] }) { return <article className="overflow-hidden rounded-2xl" style={{ background: "var(--bg-card)", border: "1px solid var(--border-default)" }}><div className="flex items-center justify-between gap-3 border-b px-5 py-4" style={{ borderColor: "var(--border-dim)" }}><div><h2 className="text-sm font-bold">Recent task activity</h2><p className="mt-1 text-xs" style={{ color: "var(--text-muted)" }}>Smara execution records only; task content is not exposed.</p></div><Boundary tone="blue">Smara data</Boundary></div>{rows.length ? <div className="overflow-x-auto"><table className="w-full min-w-[640px] text-left text-xs"><thead style={{ color: "var(--text-muted)" }}><tr><th className="px-5 py-3 font-semibold">Task</th><th className="px-5 py-3 font-semibold">Status</th><th className="px-5 py-3 font-semibold">Account</th><th className="px-5 py-3 font-semibold">Updated</th></tr></thead><tbody>{rows.map((row) => { const status = textValue(row.status, "unknown"); const tone = statusTone(status); return <tr key={String(row.task_id)} className="border-t" style={{ borderColor: "var(--border-dim)" }}><td className="max-w-[260px] truncate px-5 py-3 font-semibold">{textValue(row.title, "Untitled task")}</td><td className="px-5 py-3"><span className="inline-flex items-center gap-1.5" style={{ color: tone.color }}>{tone.icon}{status}</span></td><td className="px-5 py-3 font-mono text-[10px]" style={{ color: "var(--text-muted)" }}>{textValue(row.account_id)}</td><td className="px-5 py-3" style={{ color: "var(--text-muted)" }}>{ago(row.updated_at)}</td></tr>; })}</tbody></table></div> : <p className="px-5 py-10 text-center text-sm" style={{ color: "var(--text-muted)" }}>No task activity yet.</p>}</article>; }

function RecentEvents({ rows }: { rows: AnyRecord[] }) { return <article className="rounded-2xl p-5" style={{ background: "var(--bg-card)", border: "1px solid var(--border-default)" }}><h2 className="text-sm font-bold">Live event trail</h2><p className="mt-1 text-xs" style={{ color: "var(--text-muted)" }}>Lifecycle signals from the Smara task graph.</p><div className="mt-4 space-y-3">{rows.slice(0, 8).map((row, index) => <div key={`${String(row.task_id)}-${String(row.type)}-${index}`} className="flex items-center justify-between gap-3 text-xs"><span className="flex min-w-0 items-center gap-2"><span className="h-1.5 w-1.5 shrink-0 rounded-full" style={{ background: "var(--accent2)" }} /><span className="truncate">{textValue(row.type)}</span></span><span className="shrink-0" style={{ color: "var(--text-muted)" }}>{ago(row.created_at)}</span></div>)}</div></article>; }

function IntegrationSummary({ rows }: { rows: AnyRecord[] }) { return <article className="rounded-2xl p-5" style={{ background: "var(--bg-card)", border: "1px solid var(--border-default)" }}><h2 className="text-sm font-bold">Connections</h2><p className="mt-1 text-xs" style={{ color: "var(--text-muted)" }}>Smara integration metadata; credentials are never returned.</p><div className="mt-4 space-y-2">{rows.length ? rows.map((row) => <div key={`${String(row.provider)}-${String(row.health)}`} className="flex items-center justify-between gap-3 rounded-xl px-3 py-2 text-xs" style={{ background: "var(--bg-base)" }}><span className="font-semibold">{textValue(row.provider)}</span><span style={{ color: statusTone(textValue(row.health, "unknown")).color }}>{numberValue(row.count)} · {textValue(row.health)}</span></div>) : <p className="py-5 text-sm" style={{ color: "var(--text-muted)" }}>No connections configured.</p>}</div></article>; }

function SmaraDetails({ smara }: { smara: AnyRecord }) { const statuses = Object.entries((smara.tasks ?? {}) as AnyRecord).filter(([key]) => key !== "total" && key !== "active" && key !== "with_results"); return <section className="grid gap-5 lg:grid-cols-2"><PlaneCard title="Task state distribution" boundary="Counts are derived from Smara's durable task graph." icon={<Activity size={18} />} rows={statuses.map(([key, value]) => [key.replaceAll("_", " "), numberValue(value)])} /><PlaneCard title="Safety signals" boundary="Operational signals that need review; no actions are taken here." icon={<ShieldCheck size={18} />} rows={[["Results available", numberValue((smara.tasks as AnyRecord | undefined)?.with_results)], ["Unresolved dead letters", numberValue((smara.dead_letters as AnyRecord | undefined)?.unresolved)], ["Paired executors", numberValue((smara.executors as AnyRecord | undefined)?.total)], ["Connected integrations", numberValue((smara.integrations as AnyRecord | undefined)?.total)]]} /></section>; }

function SyntarusView({ syntarus }: { syntarus: AnyRecord }) { const status = textValue(syntarus.status, "unconfigured"); const tone = statusTone(status); return <section className="grid gap-5 lg:grid-cols-[.8fr_1.2fr]"><article className="rounded-2xl p-6" style={{ background: "var(--bg-card)", border: "1px solid var(--border-default)" }}><div className="flex items-center justify-between gap-3"><span className="grid h-11 w-11 place-items-center rounded-2xl" style={{ color: "#93a9ff", background: "rgba(147,169,255,.12)" }}><Database size={20} /></span><Boundary tone={status === "healthy" ? "green" : "gold"}>{status}</Boundary></div><h2 className="mt-5 text-xl font-bold">Syntarus context plane</h2><p className="mt-2 text-sm leading-6" style={{ color: "var(--text-secondary)" }}>Syntarus remains an independent memory service. Smara talks to it through the SDK/API boundary; this console does not merge its records into Smara task storage.</p><div className="mt-6 rounded-xl p-3 text-xs leading-5" style={{ color: "var(--text-muted)", background: "var(--bg-base)" }}>{textValue(syntarus.detail, "No health detail available.")}</div></article><article className="rounded-2xl p-6" style={{ background: "var(--bg-card)", border: "1px solid var(--border-default)" }}><div className="flex items-center gap-2" style={{ color: tone.color }}>{tone.icon}<h2 className="text-sm font-bold">Boundary and probe details</h2></div><div className="mt-5 grid gap-3 sm:grid-cols-2">{[["SDK configured", syntarus.sdk_configured ? "Yes" : "No"], ["Health probe", `${textValue(syntarus.latency_ms, "—")} ms`], ["Health path", textValue(syntarus.health_path)], ["Raw memory", syntarus.raw_memory_exposed ? "Exposed" : "Not exposed"]].map(([label, value]) => <div key={label} className="rounded-xl p-3" style={{ background: "var(--bg-base)" }}><div className="text-[10px] uppercase tracking-[0.12em]" style={{ color: "var(--text-muted)" }}>{label}</div><div className="mt-2 text-sm font-semibold">{value}</div></div>)}</div><div className="mt-6"><Boundary tone="blue">Syntarus data stays separate</Boundary></div></article></section>; }

function PeopleView({ people, accounts }: { people: AnyRecord[]; accounts: AnyRecord }) { return <section className="overflow-hidden rounded-2xl" style={{ background: "var(--bg-card)", border: "1px solid var(--border-default)" }}><div className="flex items-center justify-between gap-3 border-b px-5 py-4" style={{ borderColor: "var(--border-dim)" }}><div><h2 className="text-sm font-bold">Accounts & access</h2><p className="mt-1 text-xs" style={{ color: "var(--text-muted)" }}>Identity and operational metadata only. Secrets, OAuth grants, and memory text are excluded.</p></div><Boundary tone="blue">{numberValue(accounts.total)} accounts</Boundary></div>{people.length ? <div className="overflow-x-auto"><table className="w-full min-w-[760px] text-left text-xs"><thead style={{ color: "var(--text-muted)" }}><tr><th className="px-5 py-3">Account</th><th className="px-5 py-3">Plan</th><th className="px-5 py-3">Tasks</th><th className="px-5 py-3">Last login</th><th className="px-5 py-3">Created</th></tr></thead><tbody>{people.map((person) => <tr key={String(person.account_id)} className="border-t" style={{ borderColor: "var(--border-dim)" }}><td className="px-5 py-3"><div className="font-semibold">{textValue(person.display_name, textValue(person.email, "Unnamed account"))}</div><div className="mt-1 font-mono text-[10px]" style={{ color: "var(--text-muted)" }}>{textValue(person.account_id)}</div></td><td className="px-5 py-3">{textValue(person.plan, "free")}</td><td className="px-5 py-3">{numberValue(person.tasks_active)} active · {numberValue(person.tasks_total)} total</td><td className="px-5 py-3" style={{ color: "var(--text-muted)" }}>{ago(person.last_login_at)}</td><td className="px-5 py-3" style={{ color: "var(--text-muted)" }}>{dateValue(person.created_at)}</td></tr>)}</tbody></table></div> : <p className="px-5 py-10 text-center text-sm" style={{ color: "var(--text-muted)" }}>No account records are available.</p>}</section>; }
