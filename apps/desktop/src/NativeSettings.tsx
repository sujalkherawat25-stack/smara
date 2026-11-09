import { useState } from "react";
import { nativeProbe, nativeSettings, type NativeSetup } from "./nativeApi";
import type { LocalModelProfile } from "./types";
import { ANY_SITE, browsesAnySite, modelPayload, profileId } from "./settingsModel";
import NativeTelegramSettings from "./NativeTelegramSettings";
import NativeMemorySettings from "./NativeMemorySettings";
import NativeEmail from "./NativeEmail";
import NativeBackground from "./NativeBackground";
import NativeAutomation from "./NativeAutomation";
import NativeBrowser from "./NativeBrowser";

const aliases: Record<string, string> = { tavily: "TAVILY_API_KEY", exa: "EXA_API_KEY", serper: "SERPER_API_KEY", brave: "BRAVE_SEARCH_API_KEY" };
const emptyModel = { id: "", label: "", provider: "", base_url: "", model: "", api_key: "", auth_header: "authorization", context_window: "" as string | number | null };
type Tab = "models" | "memory" | "browser" | "tools" | "email" | "background" | "telegram" | "help";
const NAV: { id: Tab; label: string }[] = [
  { id: "models", label: "Models" },
  { id: "memory", label: "Memory" },
  { id: "browser", label: "Browser & computer" },
  { id: "tools", label: "Search & tools" },
  { id: "email", label: "Email" },
  { id: "background", label: "Background" },
  { id: "telegram", label: "Telegram" },
  { id: "help", label: "Help & setup" },
];
type SettingsProps = {
  initialTab?: "models" | "memory" | "tools" | "browser";
  setup: NativeSetup; locked: boolean; version: string; runtimeBuilt: boolean;
  onUpdate: (value: NativeSetup) => void; onClose: () => void;
  onPendingChange: (pending: boolean) => void;
  toolsEnabled: boolean; workersEnabled: boolean; browserOrigins: string;
  onToolsChange: (enabled: boolean) => void; onWorkersChange: (enabled: boolean) => void; onOriginsChange: (origins: string) => void;
  /** Runs one settings operation. The parent stops the chat runtime first only when the change needs it. */
  call?: (operation: string, args: Record<string, unknown>) => Promise<NativeSetup>;
};
export default function NativeSettings({ setup, locked, onUpdate, onClose, onPendingChange, version, runtimeBuilt, toolsEnabled, workersEnabled, browserOrigins, onToolsChange, onWorkersChange, onOriginsChange, initialTab = "models", call }: SettingsProps) {
  const [tab, setTab] = useState<Tab>(initialTab);
  const [model, setModel] = useState(emptyModel);
  const [editing, setEditing] = useState(false);
  const [provider, setProvider] = useState(setup.preferences.search_provider);
  const [secret, setSecret] = useState("");
  const [pending, setPending] = useState(false);
  const [integrationPending, setIntegrationPending] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [probePending, setProbePending] = useState("");
  const disabled = locked || pending || integrationPending;
  const activeModel = setup.profiles.find(profile => profile.id === setup.preferences.active_model);
  const origins = browserOrigins.split(/\s+/).filter(Boolean);
  const anySite = browsesAnySite(browserOrigins);
  const integrationBusy = (value: boolean) => { setIntegrationPending(value); onPendingChange(value); };
  const save = async (operation: string, args: Record<string, unknown>) => {
    if (pending || (locked && operation !== "bootstrap")) return;
    onPendingChange(true); setPending(true); setError(""); setNotice("");
    try { onUpdate(await (call ?? nativeSettings)(operation, args)); setNotice("Saved. Memory and search apply right away; model, tool and site changes apply to your next message."); }
    catch (reason) { setError(String(reason)); }
    finally { onPendingChange(false); setPending(false); setSecret(""); setModel(current => ({ ...current, api_key: "" })); }
  };
  const saveOptions = (next: { tools: boolean; workers: boolean; origins: string[] }) => save("save_project_options", { workspace: setup.preferences.workspace, options: { tools_enabled: next.tools, workers_enabled: next.workers, browser_origins: next.origins } });
  const edit = (profile: LocalModelProfile) => { setModel({ ...emptyModel, ...profile, context_window: profile.context_window ?? "", api_key: "" }); setEditing(true); setNotice(""); };
  const runProbe = async (operation: "test_model" | "test_search") => {
    if (disabled || probePending) return;
    const explanation = operation === "test_model"
      ? "Smara will send one fixed ‘Reply exactly OK’ request with a 256-token maximum (low reasoning for Sarvam reasoning models). It will not send chat history or project files. Your provider may charge for the request. Continue?"
      : "Smara will send the fixed query ‘Smara web search connectivity test’ to the selected search provider, requesting at most one result. Your provider may charge for the request. Continue?";
    if (!window.confirm(explanation)) return;
    setPending(true); setProbePending(operation); onPendingChange(true); setError(""); setNotice("");
    try {
      const result = await nativeProbe(operation, operation === "test_model" ? { id: activeModel?.id } : {});
      setNotice(operation === "test_model"
        ? `Model responded: ${result.model} · ${result.elapsed_ms} ms.`
        : `Search responded: ${result.provider} · ${result.result_count} result(s) for the fixed test query.`);
    } catch (reason) { setError(String(reason)); }
    finally { onPendingChange(false); setPending(false); setProbePending(""); }
  };
  const choose = (value: Tab) => { if (pending || integrationPending) return; setTab(value); setSecret(""); setEditing(false); setModel(current => ({ ...current, api_key: "" })); setError(""); setNotice(""); };
  const memoryOn = !!setup.memory?.configured && !!setup.memory?.enabled;
  return <section className="native-settings">
    <aside className="settings-nav">
      <button className="settings-back" disabled={pending || integrationPending} onClick={onClose}>Back to chat</button>
      <h2 className="settings-title">Settings</h2>
      <nav aria-label="Settings sections">{NAV.map(item => <button disabled={pending || integrationPending} key={item.id} className={tab === item.id ? "selected" : ""} onClick={() => choose(item.id)}><span>{item.label}</span>{item.id === "memory" && <small>{memoryOn ? "On" : "Not connected"}</small>}</button>)}</nav>
      <small className="settings-version">Version {version}</small>
    </aside>
    <div className="settings-body">
      {locked && <p className="native-warning">Finishing session setup. Settings will be available shortly.</p>}
      {error && <p role="alert" className="native-error">{error.replaceAll("Settings → Tools", "Search & tools")}</p>}{notice && <p role="status" className="settings-notice">{notice}</p>}

      {tab === "models" && <div className="set-page">
        <header><h2>Models</h2><p>The AI providers Smara can use. Pick the active one from the box next to the message field. Smara never silently swaps your model.</p></header>
        <div className="set-group">
          {!setup.profiles.length && <div className="set-row"><div><strong>No model yet</strong><small>Add your provider below to start chatting.</small></div></div>}
          {setup.profiles.map(profile => <div className="set-row" key={profile.id}><div><strong>{profile.label}{profile.id === setup.preferences.active_model && <span className="set-badge">Active</span>}</strong><small>{profile.model} · {profile.provider}</small></div><button disabled={disabled} onClick={() => edit(profile)}>Edit</button></div>)}
          <div className="set-row"><button disabled={disabled} onClick={() => { setModel(emptyModel); setEditing(true); }}>Add model</button></div>
        </div>
        {(editing || !setup.profiles.length) && <form className="set-group settings-form" onSubmit={event => { event.preventDefault(); const known = setup.profiles.map(p => p.id); const id = model.id || profileId(model.label, known); void save("save_model", { profile: modelPayload({ ...model, id }) }).then(() => setEditing(false)); }}>
          <div className="set-row stack"><strong>{setup.profiles.some(p => p.id === model.id) ? "Edit model" : "Add model"}</strong>
            <label>Display name<input required disabled={disabled} placeholder="Sarvam GLM" value={model.label} onChange={e => setModel({ ...model, label: e.target.value })} /></label>
            <label>Provider<input required disabled={disabled} placeholder="sarvam" value={model.provider} onChange={e => setModel({ ...model, provider: e.target.value })} /></label>
            <label>Endpoint<input required disabled={disabled} placeholder="https://api.sarvam.ai/v2" value={model.base_url} onChange={e => setModel({ ...model, base_url: e.target.value })} /></label>
            <label>Model ID<input required disabled={disabled} placeholder="Your provider's exact model ID" value={model.model} onChange={e => setModel({ ...model, model: e.target.value })} /></label>
            <label>API key<input type="password" autoComplete="off" disabled={disabled} value={model.api_key} placeholder="Leave blank when editing to keep the saved key" onChange={e => setModel({ ...model, api_key: e.target.value })} /></label>
            <small>Encrypted with Windows DPAPI; key values are never shown again. Only chat-compatible APIs are supported.</small>
            <details><summary>Advanced</summary>
              <label>Profile ID<input disabled={disabled || setup.profiles.some(p => p.id === model.id)} placeholder="Made from the display name" value={model.id} onChange={e => setModel({ ...model, id: e.target.value })} /></label>
              <label>Authentication<select disabled={disabled} value={model.auth_header} onChange={e => setModel({ ...model, auth_header: e.target.value })}><option value="authorization">Bearer authorization</option><option value="api-subscription-key">Sarvam · api-subscription-key</option></select></label>
              <label>Context window (tokens)<input inputMode="numeric" disabled={disabled} placeholder="Blank = assume 128,000" value={model.context_window ?? ""} onChange={e => setModel({ ...model, context_window: e.target.value.replace(/[^0-9]/g, "") })} /></label>
              <small>Smara summarises older turns automatically at 80% of this size so long chats keep working. Set it to your model's real limit.</small>
            </details>
            <div className="set-actions"><button className="primary" disabled={disabled}>Save model</button>{!!setup.profiles.length && <button type="button" disabled={disabled} onClick={() => setEditing(false)}>Cancel</button>}</div>
          </div>
        </form>}
      </div>}

      {tab === "memory" && <NativeMemorySettings setup={setup} locked={disabled} onUpdate={onUpdate} onPendingChange={integrationBusy} />}

      {tab === "browser" && <div className="set-page">
        <header><h2>Browser & computer</h2><p>Let Smara use websites and Windows apps for you. It asks before anything that pays, sends, submits, deletes or signs in.</p></header>
        <div className="set-group">
          <div className="set-row"><div><strong>Browse any public website</strong><small>Smara can open and use any public site in chats for this project. Private and local network addresses stay blocked.</small></div>
            <label className="set-switch"><input type="checkbox" aria-label="Browse any public website" disabled={disabled} checked={anySite} onChange={event => { const next = event.target.checked ? ANY_SITE : ""; onOriginsChange(next); void saveOptions({ tools: toolsEnabled, workers: workersEnabled, origins: next ? [next] : [] }); }} /><span /></label></div>
          {!anySite && <div className="set-row stack"><label>Or only these sites<input aria-label="Approved public browser origins" placeholder="https://example.com https://another.site" disabled={disabled} value={browserOrigins} onChange={event => onOriginsChange(event.target.value)} /></label>
            <small>Separate sites with spaces. Leave empty to turn browsing off.</small>
            <div className="set-actions"><button disabled={disabled} onClick={() => void saveOptions({ tools: toolsEnabled, workers: workersEnabled, origins })}>Save sites</button></div></div>}
        </div>
        <NativeBrowser locked={disabled} />
        <NativeAutomation workspace={setup.preferences.workspace} locked={disabled} onPendingChange={integrationBusy} onSelectionChange={async () => onUpdate(await nativeSettings())} />
      </div>}

      {tab === "tools" && <div className="set-page">
        <header><h2>Search & tools</h2><p>Web search and what Smara may use in this project.</p></header>
        <section className="set-group project-options">
          <div className="set-row"><div><strong>Academic search & project notes</strong><small>Reads local project notes and academic search results. The clock and Sarvam image/OCR tools are always available; document uploads ask for approval.</small></div>
            <label className="set-switch"><input type="checkbox" aria-label="Academic search and project notes" checked={toolsEnabled} disabled={disabled} onChange={event => { onToolsChange(event.target.checked); void saveOptions({ tools: event.target.checked, workers: workersEnabled, origins }); }} /><span /></label></div>
          <div className="set-row"><div><strong>Parallel coding assistants</strong><small>Work in isolated copies of committed code. Changes stay separate for your review.</small></div>
            <label className="set-switch"><input type="checkbox" aria-label="Parallel coding assistants" checked={workersEnabled} disabled={disabled} onChange={event => { onWorkersChange(event.target.checked); void saveOptions({ tools: toolsEnabled, workers: event.target.checked, origins }); }} /><span /></label></div>
          <small className="set-footnote project-options-note">Saved choices are restored when you return to this project; action approvals remain required.</small>
        </section>
        <div className="settings-grid"><form className="settings-card settings-form" onSubmit={event => { event.preventDefault(); void save("save_credential", { name: aliases[provider], provider, secret }); }}><h2>Web search</h2><p>Pick a provider and save its key. Smara can read public pages to verify results.</p>
          <label>Search provider<select value={provider} disabled={disabled} onChange={e => { setProvider(e.target.value); setSecret(""); }}>{Object.keys(aliases).map(p => <option key={p} value={p}>{p[0].toUpperCase() + p.slice(1)}</option>)}</select></label>
          <button type="button" disabled={disabled} onClick={() => void save("select_search", { provider })}>Use this provider</button>
          <label>{aliases[provider]}<input type="password" autoComplete="off" disabled={disabled} value={secret} onChange={e => setSecret(e.target.value)} placeholder="Paste your search provider API key" /></label><button className="primary" disabled={disabled || !secret.trim()}>Save search key</button>
          <small>Saving the key also selects this provider. Keys are encrypted in Smara’s local credential store. Smara does not load project .env files. Search queries go only to the selected provider.</small>
        </form><section className="settings-card"><h2>Search readiness</h2><p className={`readiness ${setup.search.configured ? "ready" : "attention"}`}>{setup.search.configured ? "Credential readable" : "Needs configuration"}</p><p>{setup.search.provider || "Not selected"} · {setup.search.detail.replaceAll("Settings → Tools", "Search & tools")}</p><small>Local configuration check only—not a live search test.</small>
          <h3>Saved keys</h3>{setup.credentials.length ? setup.credentials.map(c => <div className="credential-row" key={c.name}><div><code>{c.name}</code><small>{c.provider} · protected on this computer</small></div><button disabled={disabled} onClick={() => { if (window.confirm(`Remove ${c.name}? Tools/models using it will stop working until you save a replacement.`)) void save("delete_credential", { name: c.name }); }}>Remove</button></div>) : <p>No keys saved.</p>}
          <button disabled={pending} onClick={() => void save("bootstrap", {})}>Refresh readiness</button>
        </section></div>
      </div>}

      {tab === "email" && <div className="set-page"><NativeEmail workspace={setup.preferences.workspace} locked={disabled} onUpdate={onUpdate} onPendingChange={integrationBusy} /></div>}
      {tab === "background" && <div className="set-page"><NativeBackground workspace={setup.preferences.workspace} profile={setup.preferences.active_model} locked={disabled} onPendingChange={integrationBusy} /></div>}
      {tab === "telegram" && <div className="set-page"><NativeTelegramSettings workspace={setup.preferences.workspace} profile={setup.preferences.active_model} locked={disabled} onSaved={() => { void nativeSettings().then(onUpdate); }} /></div>}

      {tab === "help" && <div className="set-page">
        <header><h2>Help & setup</h2><p>Smara {version} · Native runtime: <strong>{runtimeBuilt ? "available" : "not built"}</strong></p></header>
        <section className="set-group">
          <div className="set-row stack"><strong>Get started</strong>
            <ol><li>Add a chat-compatible model in <strong>Models</strong>, then choose it next to the message field.</li>
              <li>Connect <strong>Memory</strong> with your Syntarus key so Smara remembers you.</li>
              <li>Choose a trusted project folder from the sidebar. Avoid folders you do not trust.</li></ol>
            <small>Use <strong>Attach</strong> in the composer to add project files. Smara can read images and scanned PDFs with Sarvam after approval.</small></div>
          <div className="set-row stack"><strong>Checks</strong>
            <small>Models: {setup.profiles.length ? `${setup.profiles.length} saved${activeModel ? ` · active: ${activeModel.label}` : " · choose one in the composer"}` : "none yet."} Web search: {setup.search.configured ? `${setup.search.provider} key readable locally.` : "no readable key."}</small>
            <small>Saved-key checks are local only. These tests are manual, send fixed minimal requests, and may incur provider charges; no chat history or project files are included.</small>
            <div className="set-actions"><button disabled={disabled || !!probePending || !activeModel} onClick={() => void runProbe("test_model")}>{probePending === "test_model" ? "Testing model…" : "Test selected model"}</button><button disabled={disabled || !!probePending || !setup.search.configured} onClick={() => void runProbe("test_search")}>{probePending === "test_search" ? "Testing search…" : "Test web search"}</button></div>
            {!runtimeBuilt && <p role="alert">The native runtime is missing. Use a matching Desktop installer or rebuild the native runtime before starting a chat.</p>}</div>
        </section>
        <section className="set-group">
          <div className="set-row stack"><strong>Changes and commands</strong>
            <small>Open <strong>Changes</strong> above the chat to inspect diffs. Stage and unstage affect the Git index; Revert changes working files. <strong>Undo</strong> in <strong>Last turn</strong> reverses supported file changes from the last run, not chat history; Revert and Undo keep a local recovery copy.</small>
            <small><strong>Terminal</strong> starts PowerShell in the Windows MXC sandbox: writes stay inside the project and the network is off. Hosts without MXC fail closed; there is no weaker fallback.</small></div>
          <div className="set-row stack"><strong>Access and privacy</strong>
            <small><strong>Autonomous work:</strong> type <code>/goal</code> followed by the outcome. Goals pause at limits and need your acceptance before completion; Telegram controls the same goals.</small>
            <small><strong>Voice:</strong> records locally for up to 25 seconds, uploads to Sarvam only when you press Transcribe, and never sends a chat by itself.</small>
            <small><strong>Memory:</strong> Syntarus receives your messages and finished answers, and recalled context goes to your selected model. Existing history is not bulk-uploaded; disconnecting does not delete stored memories.</small>
            <ul><li>Chat requests and context you provide go to your selected model provider, including project text Smara reads for a task.</li>
              <li>Search queries go to the selected search provider; fetched public-page text may go to your model.</li>
              <li>Browser work happens in an owned browser or your own helper browser, with owner approval for anything that pays, sends, submits or signs in. Windows app control is off until you grant it for a window or for 30 minutes. Full desktop/computer control and unattended company operations are not enabled without your grant.</li></ul></div>
        </section>
      </div>}
    </div>
  </section>;
}
