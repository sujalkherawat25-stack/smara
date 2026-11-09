import NativeBrowser from "../NativeBrowser";
import { hostedStatus, type HelperSnapshot } from "../hostedStatus";

/** Hosted Smara: whether this PC is paired and running the helper, plus the existing helper controls. */
export default function HostedPanel({ snapshot, locked }: { snapshot: HelperSnapshot; locked: boolean }) {
  const status = hostedStatus(snapshot);
  const last = snapshot?.status?.last_poll;
  return (
    <div className="hosted-panel" aria-label="Hosted Smara">
      <span className="smara-chip" data-tone={status.tone}>{status.label}</span>
      {snapshot?.shop && <div>Shop: <strong>{snapshot.shop}</strong></div>}
      {snapshot?.server && <div>Server: <code>{snapshot.server}</code></div>}
      {last !== undefined && <div>Last poll: {new Date(last * 1000).toLocaleTimeString()}</div>}
      <NativeBrowser locked={locked} />
    </div>
  );
}
