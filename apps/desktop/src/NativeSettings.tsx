import { useState } from "react";
import { nativeSettings, type NativeSetup } from "./nativeApi";
import type { LocalModelProfile } from "./types";

const aliases: Record<string, string> = { tavily: "TAVILY_API_KEY", exa: "EXA_API_KEY", serper: "SERPER_API_KEY", brave: "BRAVE_SEARCH_API_KEY" };
const emptyModel = { id: "", label: "", provider: "", base_url: "", model: "", api_key: "", auth_header: "authorization" };
type SettingsProps = {
  setup: NativeSetup; locked: boolean; version: string;
  onUpdate: (value: NativeSetup) => void; onClose: () => void;
  onPendingChange: (pending: boolean) => void;
  toolsEnabled: boolean; workersEnabled: boolean; browserOrigins: string;
  onToolsChange: (enabled: boolean) => void; onWorkersChange: (enabled: boolean) => void; onOriginsChange: (origins: string) => void;
};
export default function NativeSettings({ setup, locked, onUpdate, onClose, onPendingChange, version, toolsEnabled, workersEnabled, browserOrigins, onToolsChange, onWorkersChange, onOriginsChange }: SettingsProps) {
  const [tab, setTab] = useState("models");
  const [model, setModel] = useState(emptyModel);
  const [provider, setProvider] = useState(setup.preferences.search_provider);
  const [secret, setSecret] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const disabled = locked || pending;
  const save = async (operation: string, args: Record<string, unknown>) => {
    if (pending || (locked && operation !== "bootstrap")) return;
    onPendingChange(true); setPending(true); setError(""); setNotice("");
    try { onUpdate(await nativeSettings(operation, args)); setNotice("Saved. Changes apply when you return to chat."); }
    catch (reason) { setError(String(reason)); }
    finally { onPendingChange(false); setPending(false); setSecret(""); setModel(current => ({ ...current, api_key: "" })); }
  };
  const edit = (profile: LocalModelProfile) => { setModel({ ...emptyModel, ...profile, api_key: "" }); setNotice(""); };
  return <section className="native-settings">
    <div className="settings-heading"><div><h1>Settings</h1><p>Your models and connected services.</p></div><button disabled={pending} onClick={onClose}>← Back to chat</button></div>
    <nav className="settings-tabs" aria-label="Settings sections">{["models", "tools"].map(value => <button key={value} className={tab === value ? "selected" : ""} onClick={() => { setTab(value); setSecret(""); setModel(current => ({ ...current, api_key: "" })); }}>{value === "models" ? "Models" : "Integrations"}</button>)}</nav>
    {locked && <p className="native-warning">Finishing session setup. Settings will be available shortly.</p>}
    {error && <p role="alert" className="native-error">{error.replaceAll("Settings → Tools", "Settings → Integrations")}</p>}{notice && <p role="status" className="settings-notice">{notice}</p>}
    {tab === "models" && <div className="settings-grid"><section className="settings-card"><h2>Configured models</h2><p>These endpoints are used as selected. Smara never silently replaces your model.</p>
      {!setup.profiles.length && <p>No models configured. Add your provider on the right.</p>}
      {setup.profiles.map(profile => <div className="model-row" key={profile.id}><div><strong>{profile.label}</strong><small>{profile.model} · {profile.provider}</small><small className="endpoint">{profile.base_url}</small></div><button disabled={disabled} onClick={() => edit(profile)}>Edit</button></div>)}
      <button disabled={disabled} onClick={() => setModel(emptyModel)}>＋ Add model</button>
    </section><form className="settings-card settings-form" onSubmit={event => { event.preventDefault(); void save("save_model", { profile: model }); }}><h2>{setup.profiles.some(p => p.id === model.id) ? "Edit model" : "Add model"}</h2>
      <div className="form-pair"><label>Profile ID<input required disabled={disabled} placeholder="sarvam_glm" value={model.id} onChange={e => setModel({ ...model, id: e.target.value })} /></label><label>Display name<input required disabled={disabled} placeholder="Sarvam GLM" value={model.label} onChange={e => setModel({ ...model, label: e.target.value })} /></label></div>
      <label>Provider<input required disabled={disabled} placeholder="sarvam" value={model.provider} onChange={e => setModel({ ...model, provider: e.target.value })} /></label>
      <label>Chat-compatible endpoint<input required disabled={disabled} placeholder="https://api.sarvam.ai/v2" value={model.base_url} onChange={e => setModel({ ...model, base_url: e.target.value })} /></label>
      <label>Model ID<input required disabled={disabled} placeholder="Your provider's exact model ID" value={model.model} onChange={e => setModel({ ...model, model: e.target.value })} /></label>
      <label>Authentication<select disabled={disabled} value={model.auth_header} onChange={e => setModel({ ...model, auth_header: e.target.value })}><option value="authorization">Bearer authorization</option><option value="api-subscription-key">Sarvam · api-subscription-key</option></select></label>
      <label>API key<input type="password" autoComplete="off" disabled={disabled} value={model.api_key} placeholder="Leave blank when editing to keep the saved key" onChange={e => setModel({ ...model, api_key: e.target.value })} /></label>
      <small>Encrypted with Windows DPAPI. Key values are never returned to this screen. Only chat-compatible APIs are supported by this adapter.</small><button className="primary" disabled={disabled}>Save model</button>
    </form></div>}
    {tab === "tools" && <><section className="settings-card project-options"><h2>For this project</h2>
      <label className="native-tools-choice"><input type="checkbox" checked={toolsEnabled} disabled={disabled} onChange={event => onToolsChange(event.target.checked)} /> Web research & project memory</label><small>Enables public search, page reading, the clock and local project-memory readers. Search queries go to your selected provider; private memory is not uploaded.</small>
      <details><summary>Advanced options</summary><label className="native-tools-choice"><input type="checkbox" checked={workersEnabled} disabled={disabled} onChange={event => onWorkersChange(event.target.checked)} /> Parallel coding assistants</label><small>Work in isolated copies of committed code. Changes stay separate for your review.</small>
      <label className="native-tools-choice">Allowed public browser sites<input aria-label="Approved public browser origins" placeholder="https://example.com" disabled={disabled} value={browserOrigins} onChange={event => onOriginsChange(event.target.value)} /></label><small>A fresh browser asks before every action. No personal cookies, login, uploads or desktop control. Leave empty to disable.</small></details>
      <small className="project-options-note">These choices apply to this project session. Switching projects resets them; action approvals remain required.</small>
    </section><div className="settings-grid"><form className="settings-card settings-form" onSubmit={event => { event.preventDefault(); void save("save_credential", { name: aliases[provider], provider, secret }); }}><h2>Web search</h2><p>Choose a provider and save its key. Smara can read public pages to verify search results.</p>
      <label>Search provider<select value={provider} disabled={disabled} onChange={e => { setProvider(e.target.value); setSecret(""); }}>{Object.keys(aliases).map(p => <option key={p} value={p}>{p[0].toUpperCase() + p.slice(1)}</option>)}</select></label>
      <button type="button" disabled={disabled} onClick={() => void save("select_search", { provider })}>Use this provider</button>
      <label>{aliases[provider]}<input type="password" autoComplete="off" disabled={disabled} value={secret} onChange={e => setSecret(e.target.value)} placeholder="Paste your search provider API key" /></label><button className="primary" disabled={disabled || !secret.trim()}>Save search key</button>
      <small>Saving a key does not change the selected provider or enable tools. Environment settings, if present, take precedence. Search sends queries to the selected service.</small>
    </form><section className="settings-card"><h2>Search readiness</h2><p className={`readiness ${setup.search.configured ? "ready" : "attention"}`}>{setup.search.configured ? "● Credential readable" : "○ Needs configuration"}</p><p>{setup.search.provider || "Not selected"} · {setup.search.detail.replaceAll("Settings → Tools", "Settings → Integrations")}</p><small>Local configuration check only—not a live search test.</small>
      <h3>Saved credential aliases</h3>{setup.credentials.length ? setup.credentials.map(c => <div className="credential-row" key={c.name}><div><code>{c.name}</code><small>{c.provider} · protected on this computer</small></div><button disabled={disabled} onClick={() => { if (window.confirm(`Remove ${c.name}? Tools/models using it will stop working until you save a replacement.`)) void save("delete_credential", { name: c.name }); }}>Remove</button></div>) : <p>No keys saved.</p>}
      <button disabled={pending} onClick={() => void save("bootstrap", {})}>Refresh readiness</button>
    </section></div></>}
    <small className="settings-version">Smara {version} · Keys protected on this computer</small>
  </section>;
}
