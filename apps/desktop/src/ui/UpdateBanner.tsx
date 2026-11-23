import { useEffect, useRef, useState } from "react";
import { canInstall, updateLine, type UpdateState } from "../update";

/** Smara Desktop updates itself from Smara's server. It checks when it opens and every few hours; when a new version is
 * ready it asks once, in a small bar, and installs only when the owner presses the button (the installer checks the
 * update's signature first). Work in progress is never interrupted without that press. */
const CHECK_EVERY_MS = 6 * 60 * 60 * 1000;

type Found = { version: string; notes?: string; install: (progress: (done: number, total: number) => void) => Promise<void> };

export async function findUpdate(): Promise<Found | null> {
  const { check } = await import("@tauri-apps/plugin-updater");
  const update = await check();
  if (!update) return null;
  return {
    version: update.version, notes: update.body,
    install: async progress => {
      let total = 0, done = 0;
      await update.downloadAndInstall(event => {
        if (event.event === "Started") total = event.data.contentLength || 0;
        if (event.event === "Progress") { done += event.data.chunkLength; progress(done, total); }
      });
      const { relaunch } = await import("@tauri-apps/plugin-process");
      await relaunch();
    },
  };
}

export default function UpdateBanner({ busy, find = findUpdate }: { busy: boolean; find?: () => Promise<Found | null> }) {
  const [found, setFound] = useState<Found | null>(null);
  const [state, setState] = useState<UpdateState>("idle");
  const [percent, setPercent] = useState(0);
  const [hidden, setHidden] = useState(false);
  const findRef = useRef(find);

  useEffect(() => {
    let disposed = false;
    const look = () => { void findRef.current().then(next => { if (!disposed && next) setFound(next); }).catch(() => { /* offline: try later */ }); };
    look();
    const timer = setInterval(look, CHECK_EVERY_MS);
    return () => { disposed = true; clearInterval(timer); };
  }, []);

  if (!found || hidden) return null;
  const install = () => {
    setState("installing");
    void found.install((done, total) => setPercent(total ? Math.round(done * 100 / total) : 0)).catch(() => setState("failed"));
  };
  return <div className="update-banner" role="status">
    <span>{updateLine(state, found.version, percent)}</span>
    {state !== "installing" && <>
      <button disabled={!canInstall(state, busy)} title={busy ? "Finish or stop the current task first" : undefined} onClick={install}>{state === "failed" ? "Try again" : "Update and restart"}</button>
      <button className="link" onClick={() => setHidden(true)}>Later</button>
    </>}
  </div>;
}
