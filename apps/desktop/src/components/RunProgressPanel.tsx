import { useEffect, useState } from "react";
import { elapsedLabel, runIsQuiet } from "../chatRunState";

export function RunProgressPanel({ title, detail, lastProgressAt, activityOpen, onToggleActivity }: {
  title: string; detail?: string | null; lastProgressAt: number;
  activityOpen: boolean; onToggleActivity: () => void;
}) {
  const [startedAt] = useState(() => Date.now());
  const [now, setNow] = useState(startedAt);
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  return <section className="active-execution-pill" aria-label="Current run">
    <span className="exec-spinner" aria-hidden="true">⚡</span>
    <div className="exec-content">
      <div className="exec-title" role="status">{title}</div>
      {detail && <div className="exec-thought">{detail}</div>}
      <div className="run-progress-meta">Elapsed {elapsedLabel(now - startedAt)} · Last update {elapsedLabel(now - lastProgressAt)} ago</div>
      {runIsQuiet(now, lastProgressAt, title) && <p className="run-progress-warning">
        No new progress for {elapsedLabel(now - lastProgressAt)}. The last reported step is shown above.
        Model requests can take time; use Stop if you don’t want to keep waiting.
      </p>}
    </div>
    <button type="button" className="run-activity-toggle" onClick={onToggleActivity} aria-expanded={activityOpen}>
      {activityOpen ? "Hide activity" : "Show activity"}
    </button>
  </section>;
}
