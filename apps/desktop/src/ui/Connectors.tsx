import { useCallback, useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { connectorProblem, connectorTarget, splitArgs, type Connector, type ConnectorList } from "../connectors";

const call = (action: string, values: Record<string, unknown> = {}) =>
  invoke<ConnectorList>("native_manage", { request: { operation: "connectors", action, ...values } });

const BLANK = { label: "", kind: "http" as "http" | "stdio", url: "", command: "", args: "", token: "", envName: "", envValue: "" };

/** Services the owner plugs in. Each one is an extra set of tools; every call still asks in the chat. */
export default function Connectors({ locked }: { locked: boolean }) {
  const [list, setList] = useState<Connector[]>([]);
  const [form, setForm] = useState(BLANK);
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const refresh = useCallback(async () => { try { setList((await call("list")).connectors); } catch (reason) { setError(String(reason)); } }, []);
  useEffect(() => { void refresh(); }, [refresh]);
  const run = async (work: () => Promise<ConnectorList>, done: string) => {
    setBusy(true); setError(""); setNotice("");
    try { setList((await work()).connectors); setNotice(done); } catch (reason) { setError(String(reason)); } finally { setBusy(false); }
  };
  const problem = connectorProblem(form);
  const submit = () => run(async () => {
    const env = form.kind === "stdio" && form.envName.trim() ? { [form.envName.trim()]: form.envValue } : {};
    const result = await call("save", { label: form.label, kind: form.kind, url: form.url, command: form.command, args: splitArgs(form.args), env, token: form.token });
    setForm(BLANK); setAdding(false);
    return result;
  }, "Connected. It takes effect in your next message; every use still asks you first.");

  return <section className="settings-card connectors" aria-label="Connectors">
    <h2>Connectors</h2>
    <p>Plug in a service that offers an MCP server (Gmail, Google Sheets, Notion, Slack and more) and your agents can use it as a tool. Smara asks you in the chat before every use, and only you can add or remove one here.</p>
    {error && <p role="alert" className="native-error">{error}</p>}
    {notice && <p role="status" className="settings-notice">{notice}</p>}
    {list.length === 0 && !adding && <p className="inbox-empty">Nothing connected yet.</p>}
    <ul className="connector-list">{list.map(item => <li key={item.id} className="connector-row" data-off={item.enabled ? "false" : "true"}>
      <div><strong>{item.label}</strong><small>{connectorTarget(item)}{item.has_token ? " · access token saved" : ""}</small></div>
      <div className="connector-actions">
        <button disabled={locked || busy} onClick={() => void run(() => call("toggle", { id: item.id, enabled: !item.enabled }), item.enabled ? "Turned off." : "Turned on.")}>{item.enabled ? "Turn off" : "Turn on"}</button>
        <button className="danger-action" disabled={locked || busy} onClick={() => { if (window.confirm(`Remove ${item.label}? Agents lose its tools and the saved token is deleted.`)) void run(() => call("remove", { id: item.id }), "Removed."); }}>Remove</button>
      </div>
    </li>)}</ul>
    {!adding ? <button className="primary" disabled={locked || busy} onClick={() => setAdding(true)}>Add a connector</button>
      : <form className="settings-form" onSubmit={event => { event.preventDefault(); if (!problem) void submit(); }}>
        <label>Name<input value={form.label} autoComplete="off" placeholder="Gmail" onChange={event => setForm({ ...form, label: event.target.value })} /></label>
        <label>Type<select value={form.kind} onChange={event => setForm({ ...form, kind: event.target.value as "http" | "stdio" })}><option value="http">Web address (recommended)</option><option value="stdio">Command on this PC</option></select></label>
        {form.kind === "http"
          ? <><label>Address<input value={form.url} autoComplete="off" placeholder="https://…/mcp" onChange={event => setForm({ ...form, url: event.target.value })} /></label>
            <label>Access token <small>(if the service gave you one)</small><input type="password" value={form.token} autoComplete="off" onChange={event => setForm({ ...form, token: event.target.value })} /></label></>
          : <><label>Command<input value={form.command} autoComplete="off" placeholder="npx" onChange={event => setForm({ ...form, command: event.target.value })} /></label>
            <label>Arguments<input value={form.args} autoComplete="off" placeholder="-y @modelcontextprotocol/server-github" onChange={event => setForm({ ...form, args: event.target.value })} /></label>
            <label>Secret setting name <small>(optional)</small><input value={form.envName} autoComplete="off" placeholder="GITHUB_TOKEN" onChange={event => setForm({ ...form, envName: event.target.value })} /></label>
            {form.envName.trim() && <label>Its value<input type="password" value={form.envValue} autoComplete="off" onChange={event => setForm({ ...form, envValue: event.target.value })} /></label>}</>}
        <small>{problem || "Tokens are kept encrypted on this PC and never shown again. Passwords for your accounts are never asked for here."}</small>
        <div className="settings-actions"><button className="primary" disabled={busy || locked || !!problem}>{busy ? "Connecting…" : "Connect"}</button><button type="button" onClick={() => { setAdding(false); setForm(BLANK); }}>Cancel</button></div>
      </form>}
  </section>;
}
