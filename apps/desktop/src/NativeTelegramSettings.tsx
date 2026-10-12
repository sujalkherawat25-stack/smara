import { useEffect, useState } from "react";
import { nativeSettings } from "./nativeApi";
import { goalRequest, type TelegramStatus } from "./goalApi";

export default function NativeTelegramSettings({ workspace, profile, locked, onSaved }: { workspace: string; profile: string; locked: boolean; onSaved: () => void }) {
  const [status, setStatus] = useState<TelegramStatus | null>(null);
  const [token, setToken] = useState("");
  const [code, setCode] = useState("");
  const [editing, setEditing] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const refresh = async () => { const value = await goalRequest<TelegramStatus>("telegram_status"); setStatus(value); setEditing(value.options?.sandbox === "workspace-write"); };
  useEffect(() => { void refresh().catch(reason => setError(String(reason))); }, []);
  const act = async (action: string) => {
    setPending(true); setError(""); setNotice("");
    try {
      if (action === "save") {
        if (token.trim()) await nativeSettings("save_credential", { name: "SMARA_TELEGRAM_BOT_TOKEN", provider: "telegram", secret: token.trim() });
        await goalRequest("save_telegram", { workspace, profile_id: profile, sandbox: editing ? "workspace-write" : "read-only" });
        setToken(""); onSaved(); setNotice("Telegram will start new goals in this project with this model.");
      } else if (action === "pair") {
        const result = await goalRequest<{ code: string }>("pair"); setCode(result.code);
      } else {
        await goalRequest(action); if (action === "unlink") setCode("");
      }
      await refresh();
    } catch (reason) { setError(String(reason)); }
    finally { setPending(false); }
  };
  return <div className="settings-grid telegram-grid">
    <section className="settings-card settings-form">
      <h2>Telegram bot configuration</h2>
      <p>Connect your private Telegram bot to start goals, review progress, and receive results while away.</p>
      {error && <p role="alert" className="native-error">{error}</p>}
      {notice && <p role="status" className="settings-notice">{notice}</p>}
      <label>Bot token
        <input type="password" autoComplete="off" value={token} disabled={locked || pending} onChange={event => setToken(event.target.value)} placeholder="Paste bot token from @BotFather" />
      </label>
      <small>The token is encrypted with Windows DPAPI. Only your paired Telegram account can control your goals.</small>
      <label className="native-tools-choice">
        <input type="checkbox" checked={editing} disabled={locked || pending} onChange={event => setEditing(event.target.checked)} /> Allow new Telegram goals to edit this project
      </label>
      <button className="primary" disabled={locked || pending || !workspace || !profile || (!status?.configured && !token.trim())} onClick={() => void act("save")}>Save bot and bind to this project</button>
      <div className="telegram-pairing-section">
        <h3>Account pairing</h3>
        <p>Pairing authorizes your personal Telegram account to manage Smara.</p>
        <div className="settings-actions">
          <button disabled={pending || !status?.configured} onClick={() => void act("pair")}>Create pairing code</button>
          {status?.paired && <button className="danger-action" disabled={pending} onClick={() => void act("unlink")}>Unlink account</button>}
        </div>
        {code && <div className="pairing-code-banner"><p>Send this message to your bot in Telegram within 10 minutes:</p><code className="pairing-code-tag">/link {code}</code></div>}
      </div>
    </section>
    <section className="settings-card">
      <h2>Status & worker</h2>
      <div className="telegram-status-list">
        <div className="status-item">
          <span>Bot token:</span>
          <strong className={status?.configured ? "status-tag ready" : "status-tag attention"}>{status?.configured ? "● Saved" : "○ Missing"}</strong>
        </div>
        <div className="status-item">
          <span>Chat pairing:</span>
          <strong className={status?.paired ? "status-tag ready" : "status-tag attention"}>{status?.paired ? "● Paired" : "○ Not paired"}</strong>
        </div>
        <div className="status-item">
          <span>Background service:</span>
          <strong className={status?.health?.status === "running" ? "status-tag ready" : "status-tag"}>{status?.health?.status || "Stopped"}</strong>
        </div>
      </div>
      {status?.options?.workspace && <p className="telegram-binding-info">New goals use <strong>{status.options.workspace}</strong> with model <code>{status.options.profile_id}</code>.</p>}
      {status?.health?.detail && <small className="telegram-health-detail">{status.health.detail}</small>}
      <div className="settings-actions">
        <button disabled={pending || !status?.configured} onClick={() => void act("start_service")}>Start background worker</button>
        <button disabled={pending} onClick={() => void act("telegram_status")}>Refresh status</button>
      </div>
      <div className="telegram-guide">
        <h3>Quick setup</h3>
        <ol>
          <li>Open Telegram and message <code>@BotFather</code> to create a new bot (<code>/newbot</code>).</li>
          <li>Paste the HTTP API token into the Bot token field on the left and save.</li>
          <li>Click <strong>Create pairing code</strong> and send <code>/link &lt;code&gt;</code> to your bot.</li>
          <li>Send <code>/help</code> in your Telegram chat to see available commands.</li>
        </ol>
      </div>
    </section>
  </div>;
}
