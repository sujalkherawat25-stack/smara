/** Dev-only timing. Turn on with localStorage.smaraPerf = "1", then reload. Logs go to the console; nothing is sent anywhere. */
export function perfEnabled(): boolean {
  try {
    return typeof localStorage !== "undefined" && localStorage.getItem("smaraPerf") === "1";
  } catch {
    return false;
  }
}

export function perfMark(name: string, ms: number): void {
  if (perfEnabled()) console.info(`[smara-perf] ${name} ${ms.toFixed(1)} ms`);
}

/** Log every main-thread task over 100 ms, so a freeze shows up with its time and length. */
export function startLongTaskLog(): void {
  if (!perfEnabled() || typeof PerformanceObserver === "undefined") return;
  try {
    new PerformanceObserver(list => {
      for (const entry of list.getEntries()) {
        if (entry.duration > 100) console.info(`[smara-perf] long task ${entry.duration.toFixed(0)} ms at ${entry.startTime.toFixed(0)} ms`);
      }
    }).observe({ entryTypes: ["longtask"] });
  } catch {
    // This webview does not expose long-task timing; the flag then has no effect.
  }
}
