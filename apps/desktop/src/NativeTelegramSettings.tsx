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
  return <section className="settings-card settings-form"><h2>Telegram</h2><p>Start goals, check progress, send feedback, and receive results while you are away.</p>
    {error && <p role="alert" className="native-error">{error}</p>}{notice && <p role="status">{notice}</p>}
    <p>{status?.configured ? "Bot key saved" : "Needs a bot key"} · {status?.paired ? "Paired" : "Not paired"} · {status?.health?.status || "Stopped"}</p>
    {status?.options?.workspace && <small>New goals use {status.options.workspace} · {status.options.profile_id}</small>}
    {status?.health?.detail && <small>{status.health.detail}</small>}
    <label>Bot token<input type="password" autoComplete="off" value={token} disabled={locked || pending} onChange={event => setToken(event.target.value)} placeholder="Create a bot with Telegram’s @BotFather" /></label>
    <small>The token is encrypted locally. Your private paired chat can control your goals.</small>
    <label className="native-tools-choice"><input type="checkbox" checked={editing} disabled={locked || pending} onChange={event => setEditing(event.target.checked)} /> Allow new Telegram goals to edit this project</label>
    <button disabled={locked || pending || !workspace || !profile || (!status?.configured && !token.trim())} onClick={() => void act("save")}>Save bot and use this project</button>
    <div className="settings-actions"><button disabled={pending || !status?.configured} onClick={() => void act("pair")}>Create pairing code</button><button disabled={pending || !status?.paired} onClick={() => void act("unlink")}>Unlink chat</button><button disabled={pending || !status?.configured} onClick={() => void act("start_service")}>Start background work</button><button disabled={pending} onClick={() => void act("telegram_status")}>Refresh</button></div>
    {code && <p>Send <code>/link {code}</code> to your bot in a private chat within 10 minutes.</p>}
    <small>Use /help in Telegram. The laptop must be awake and online. Goals also appear in Desktop and CLI. Native approvals are answered in Desktop → Goals.</small>
  </section>;
}
