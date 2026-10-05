import { useEffect, useState } from "react";
import { desktop } from "../api";
import type { SessionApproval, SessionProtocolSnapshot } from "../types";

export function SessionApprovalPanel({ threadId, active, workspace }: { threadId: string; active: boolean; workspace?: string }) {
  const [approvals, setApprovals] = useState<SessionApproval[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let disposed = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    setApprovals([]);
    setError("");
    const read = async () => {
      try {
        const snapshot = await desktop.sessionRequest<Pick<SessionProtocolSnapshot, "pending_approvals">>("approval/list", { thread_id: threadId }, workspace);
        if (!disposed) setApprovals(snapshot.pending_approvals);
      } catch {
        // A fresh chat has no durable thread until its first turn starts.
      } finally {
        if (!disposed && active) timer = setTimeout(read, 3000);
      }
    };
    void read();
    return () => { disposed = true; if (timer) clearTimeout(timer); };
  }, [threadId, active, workspace]);
  const respond = async (approval: SessionApproval, decision: "allow" | "deny") => {
    setBusy(approval.approval_id);
    setError("");
    try {
      await desktop.sessionRequest("approval/respond", { thread_id: threadId, approval_id: approval.approval_id,
        turn_id: approval.turn_id, action_sha256: approval.action_sha256, decision }, workspace);
      setApprovals(items => items.filter(item => item.approval_id !== approval.approval_id));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally { setBusy(null); }
  };
  if (!approvals.length && !error) return null;
  return <section className="session-approval-panel" aria-label="Action approval">
    {approvals.map(approval => <div key={approval.approval_id}>
      <strong>Smara needs permission to run {approval.action.name.replaceAll("_", " ")}</strong>
      <pre>{JSON.stringify(approval.action.arguments, null, 2)}</pre>
      <button type="button" disabled={busy !== null} onClick={() => void respond(approval, "allow")}>Allow this action</button>
      <button type="button" disabled={busy !== null} onClick={() => void respond(approval, "deny")}>Deny</button>
    </div>)}
    {error && <p role="alert">{error}</p>}
  </section>;
}
