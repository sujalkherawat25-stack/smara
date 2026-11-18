import { useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { headline, lastStep, untilLabel, type BarState } from "./controlBarView";

/** Always-on-top bar shown while Smara may control apps on this PC. Pause and Stop take effect within a step. */
export default function ControlBar() {
  const [state, setState] = useState<BarState | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let live = true;
    const tick = () => invoke<BarState>("computer_bar_state").then(value => { if (live) setState(value); }).catch(() => undefined);
    void tick();
    const timer = window.setInterval(tick, 400);
    return () => { live = false; window.clearInterval(timer); };
  }, []);
  const act = (command: "computer_bar_pause" | "computer_bar_stop", args: Record<string, unknown> = {}) =>
    invoke<BarState>(command, args).then(setState).catch(reason => setError(String(reason)));
  if (!state) return null;
  const step = lastStep(state, Date.now() / 1000);
  return <div className={state.paused ? "control-bar paused" : "control-bar"} role="status" aria-live="polite">
    <span className="control-dot" aria-hidden="true" />
    <span className="control-text"><b>{headline(state)}</b>{step && <> · {step}</>}{!step && untilLabel(state) && <> · {untilLabel(state)}</>}</span>
    {error && <span className="control-error">{error}</span>}
    <button onClick={() => void act("computer_bar_pause", { paused: !state.paused })}>{state.paused ? "Resume" : "Pause"}</button>
    <button className="control-stop" title={state.hotkey_ok ? `Or press ${state.hotkey}` : undefined} onClick={() => void act("computer_bar_stop")}>Stop</button>
    {state.hotkey_ok && <small>{state.hotkey}</small>}
  </div>;
}
