import { useState } from "react";
import { nativeSettings, type NativeSetup } from "./nativeApi";
import type { LocalModelProfile } from "./types";

const aliases: Record<string, string> = { tavily: "TAVILY_API_KEY", exa: "EXA_API_KEY", serper: "SERPER_API_KEY", brave: "BRAVE_SEARCH_API_KEY" };
const emptyModel = { id: "", label: "", provider: "", base_url: "", model: "", api_key: "", auth_header: "authorization" };
export default function NativeSettings({ setup, locked, onUpdate, onClose }: { setup: NativeSetup; locked: boolean; onUpdate: (value: NativeSetup) => void; onClose: () => void }) {
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
    setPending(true); setError(""); setNotice("");
    try { onUpdate(await nativeSettings(operation, args)); setNotice("Saved on this computer. Reconnect to use the updated configuration."); }
    catch (reason) { setError(String(reason)); }
    finally { setPending(false); setSecret(""); setModel(current => ({ ...current, api_key: "" })); }
  };
  const edit = (profile: LocalModelProfile) => { setModel({ ...emptyModel, ...profile, api_key: "" }); setNotice(""); };
  return <section className="native-settings">
    <div className="settings-heading"><div><span className="eyebrow">YOUR SETUP</span><h1>Settings</h1><p>One native harness. Your choice of models and tools.</p></div><button onClick={onClose}>← Back to chat</button></div>
    <nav className="settings-tabs" aria-label="Settings sections">{["models", "tools", "capabilities"].map(value => <button key={value} className={tab === value ? "selected" : ""} onClick={() => { setTab(value); setSecret(""); setModel(current => ({ ...current, api_key: "" })); }}>{value === "models" ? "AI models" : value === "tools" ? "Tools & credentials" : "Capabilities"}</button>)}</nav>
    {locked && <p className="native-warning">Disconnect before editing model or tool configuration. Your saved conversations are preserved.</p>}
    {error && <p role="alert" className="native-error">{error}</p>}{notice && <p role="status" className="settings-notice">{notice}</p>}
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
    {tab === "tools" && <div className="settings-grid"><form className="settings-card settings-form" onSubmit={event => { event.preventDefault(); void save("save_credential", { name: aliases[provider], provider, secret }); }}><h2>Web search</h2><p>Choose a provider and save its key. Results are discovery leads; Smara can read public pages to verify them.</p>
      <label>Search provider<select value={provider} disabled={disabled} onChange={e => { setProvider(e.target.value); setSecret(""); }}>{Object.keys(aliases).map(p => <option key={p} value={p}>{p[0].toUpperCase() + p.slice(1)}</option>)}</select></label>
      <button type="button" disabled={disabled} onClick={() => void save("select_search", { provider })}>Use this provider</button>
      <label>{aliases[provider]}<input type="password" autoComplete="off" disabled={disabled} value={secret} onChange={e => setSecret(e.target.value)} placeholder="Paste your search provider API key" /></label><button className="primary" disabled={disabled || !secret.trim()}>Save search key</button>
      <small>Saving a key does not change the selected provider or enable tools. Environment settings, if present, take precedence. Search sends queries to the selected service.</small>
    </form><section className="settings-card"><h2>Search readiness</h2><p className={`readiness ${setup.search.configured ? "ready" : "attention"}`}>{setup.search.configured ? "● Credential readable" : "○ Needs configuration"}</p><p>{setup.search.provider || "Not selected"} · {setup.search.detail}</p><small>Local configuration check only—not a live search test.</small>
      <h3>Saved credential aliases</h3>{setup.credentials.length ? setup.credentials.map(c => <div className="credential-row" key={c.name}><div><code>{c.name}</code><small>{c.provider} · protected on this computer</small></div><button disabled={disabled} onClick={() => { if (window.confirm(`Remove ${c.name}? Tools/models using it will stop working until you save a replacement.`)) void save("delete_credential", { name: c.name }); }}>Remove</button></div>) : <p>No keys saved.</p>}
      <button disabled={pending} onClick={() => void save("bootstrap", {})}>Refresh readiness</button>
    </section></div>}
    {tab === "capabilities" && <section className="settings-card capability-list"><h2>What this app actually supports</h2>
      <div><strong>Coding & terminal</strong><p>Native reason–act loop, file edits, command output, sessions, Stop/resume and request-scoped approvals. Writes are sandboxed to the active project.</p></div>
      <div><strong>Public research & local memory</strong><p>Opt-in search, public page reading, academic discovery, actual clock and paginated project memory. No private remote-memory upload.</p></div>
      <div><strong>Parallel coding workers</strong><p>Opt-in isolated checkouts from committed HEAD. Their status and permissions stay visible; changes are never automatically merged.</p></div>
      <div><strong>Public DOM browser</strong><p>Only origins you explicitly enter. A fresh browser with per-action confirmation; no personal cookies, login or uploads.</p></div>
      <div><strong>Not included yet</strong><p>Host screenshot/computer control, your personal-browser sessions, unrestricted company actions and full Codex Desktop feature parity.</p></div>
      <p>Legacy engines and panels are not available in this app. Historical files remain on disk; they are not silently converted into native sessions.</p>
    </section>}
  </section>;
}
