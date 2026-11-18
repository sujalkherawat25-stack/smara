/** Pure helpers for the "Smara is controlling your PC" bar. */
export type BarState = {
  active: boolean; paused: boolean; until: number; app: string; title: string; action: string; last_ts: number;
  hotkey: string; hotkey_ok: boolean;
};

const ACTIONS: Record<string, string> = {
  click: "clicking", double_click: "double-clicking", right_click: "right-clicking", type_text: "typing",
  press_key: "pressing a key", scroll: "scrolling", drag: "dragging", set_value: "filling a box",
};
const APPS: Record<string, string> = {
  "excel.exe": "Excel", "tallyprime.exe": "TallyPrime", "tally.exe": "Tally", "explorer.exe": "File Explorer",
  "notepad.exe": "Notepad", "calc.exe": "Calculator", "calculatorapp.exe": "Calculator", "winword.exe": "Word",
};

export function appName(exe: string): string {
  return APPS[exe] ?? exe.replace(/\.exe$/i, "");
}

/** Main line of the bar. */
export function headline(state: BarState): string {
  if (state.paused) return "Smara is paused";
  return "Smara is controlling your PC";
}

/** What it did last, if recent (2 minutes). */
export function lastStep(state: BarState, nowSeconds: number): string {
  if (!state.action || !state.app || nowSeconds - state.last_ts > 120) return "";
  return `${ACTIONS[state.action] ?? state.action.replace(/_/g, " ")} in ${appName(state.app)}`;
}

export function untilLabel(state: BarState): string {
  if (!state.until) return "";
  const d = new Date(state.until * 1000);
  return `until ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

export function isControlBar(hash: string): boolean {
  return hash === "#control-bar";
}
