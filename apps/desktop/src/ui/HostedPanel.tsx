import { useEffect, useState } from "react";
import NativeBrowser from "../NativeBrowser";
import SignInCard from "./SignInCard";
import { hostedStatus, type HelperSnapshot } from "../hostedStatus";
import { channelLines, healthUrl, type ChannelHealth } from "../channelStatus";
import { overviewLines } from "../shopOverview";

/** How often the owner channels are re-read from the server's public health page. */
const CHANNEL_REFRESH_MS = 60_000;

/** Hosted Smara: one Sign-in card on top; the shop's numbers, channels and browser helper controls sit under Details. */
export default function HostedPanel({ snapshot, locked, onChange }: { snapshot: HelperSnapshot; locked: boolean; onChange?: () => void }) {
  const status = hostedStatus(snapshot);
  const server = snapshot?.server;
  const shopLines = overviewLines(snapshot?.overview, snapshot?.now ?? Date.now() / 1000);
  const [health, setHealth] = useState<ChannelHealth>(undefined);
  useEffect(() => {
    let stopped = false;
    const load = async () => {
      try {
        const res = await fetch(healthUrl(server), { cache: "no-store" });
        const body = (await res.json()) as ChannelHealth;
        if (!stopped) setHealth(body);
      } catch {
        if (!stopped) setHealth(null);
      }
    };
    void load();
    const timer = window.setInterval(() => void load(), CHANNEL_REFRESH_MS);
    return () => {
      stopped = true;
      window.clearInterval(timer);
    };
  }, [server]);
  return (
    <div className="hosted-panel" aria-label="Hosted Smara">
      <header className="task-board-head"><div><h2>Smara account</h2><p>Connect this PC to your Smara shop.</p></div></header>
      <SignInCard helperLabel={status.label} locked={locked} onChange={() => onChange?.()} />
      <details className="hosted-details">
        <summary>Details and browser controls</summary>
        {(shopLines.length > 0 || channelLines(health).length > 0) && (
          <div className="hosted-channels" aria-label="Your shop on Smara">
            {shopLines.map((line) => <span key={line.label} className="smara-chip" data-tone={line.tone}>{line.label}</span>)}
            {channelLines(health).map((line) => <span key={line.label} className="smara-chip" data-tone={line.tone}>{line.label}</span>)}
          </div>
        )}
        <NativeBrowser locked={locked} />
      </details>
    </div>
  );
}
