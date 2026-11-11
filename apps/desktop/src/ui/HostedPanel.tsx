import { useEffect, useState } from "react";
import NativeBrowser from "../NativeBrowser";
import { hostedStatus, type HelperSnapshot } from "../hostedStatus";
import { channelLines, healthUrl, type ChannelHealth } from "../channelStatus";
import { overviewLines } from "../shopOverview";

/** How often the owner channels are re-read from the server's public health page. */
const CHANNEL_REFRESH_MS = 60_000;

/** Hosted Smara: whether this PC is paired and running the helper, the owner channels, plus the helper controls. */
export default function HostedPanel({ snapshot, locked }: { snapshot: HelperSnapshot; locked: boolean }) {
  const status = hostedStatus(snapshot);
  const last = snapshot?.status?.last_poll_at;
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
      <span className="smara-chip" data-tone={status.tone}>{status.label}</span>
      {snapshot?.shop && <div>Shop: <strong>{snapshot.shop}</strong></div>}
      {snapshot?.server && <div>Server: <code>{snapshot.server}</code></div>}
      {last !== undefined && <div>Last poll: {new Date(last * 1000).toLocaleTimeString()}</div>}
      {shopLines.length > 0 && (
        <div className="hosted-channels" aria-label="Your shop on Smara">
          {shopLines.map((line) => (
            <span key={line.label} className="smara-chip" data-tone={line.tone}>{line.label}</span>
          ))}
        </div>
      )}
      <div className="hosted-channels" aria-label="Owner channels">
        {channelLines(health).map((line) => (
          <span key={line.label} className="smara-chip" data-tone={line.tone}>{line.label}</span>
        ))}
      </div>
      <NativeBrowser locked={locked} />
    </div>
  );
}
