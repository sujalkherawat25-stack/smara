import { useCallback, useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { accessLine, type AccessState } from "../access";

/** What Smara may do on this PC right now, with one-tap Pause and Stop. Permission itself is asked in the chat. */
export default function AccessNow({ workspace, locked }: { workspace: string; locked: boolean }) {
  const [state, setState] = useState<AccessState | null>(null);
  const [now, setNow] = useState(Date.now());
  const call = useCallback(async (action: string) => {
    try { setState(await invoke<AccessState>("native_manage", { request: { operation: "computer_control", action, workspace } })); }
    catch { setState(null); }
  }, [workspace]);
  useEffect(() => { void call("status"); }, [call]);
  useEffect(() => { const timer = window.setInterval(() => setNow(Date.now()), 15000); return () => window.clearInterval(timer); }, []);
  const view = accessLine(state, now);
  return <section className="access-now" aria-label="What Smara may do right now">
    <div className="access-dot" data-on={view.on ? "true" : "false"} aria-hidden="true" />
    <div className="access-text"><strong>{view.title}</strong><small>{view.detail}</small></div>
    {view.on && <div className="access-actions">
      <button disabled={locked} onClick={() => void call(state?.control?.paused ? "resume" : "pause")}>{state?.control?.paused ? "Resume" : "Pause"}</button>
      <button className="danger" disabled={locked} onClick={() => void call("stop")}>Stop</button>
    </div>}
  </section>;
}
