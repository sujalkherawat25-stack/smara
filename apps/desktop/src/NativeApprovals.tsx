import { useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { goalRequest, type Goal, type GoalList } from "./goalApi";
import { supportsApproval } from "./nativeView";
import { approvalText } from "./taskBoardView";
import { commandWarning, uploadWarning } from "./askCard";
import { askedLabel, codeLooksRight, outcomeText, sharingText, toolLabel, type HostedList, type HostedStatus, type Outcome, type SharingStatus } from "./approvalsView";

const sharing = <T,>(action: string, values: Record<string, unknown> = {}) =>
  invoke<T>("native_manage", { request: { operation: "hosted_memory", action, ...values } });
const hosted = <T,>(action: string, values: Record<string, unknown> = {}) =>
  invoke<T>("native_manage", { request: { operation: "hosted_approvals", action, ...values } });

/** Everything that waits for the owner: Smara on Telegram (steps on this PC), background tasks, and the open chat. */
export default function NativeApprovals({ chatWaiting, onShowChat, onCount }: { chatWaiting: number; onShowChat: () => void; onCount?: (n: number) => void }) {
  const [link, setLink] = useState<HostedList | null>(null);
  const [tasks, setTasks] = useState<Goal[]>([]);
  const [code, setCode] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  const [share, setShare] = useState<SharingStatus | null>(null);

  const refresh = async () => {
    const status = await hosted<HostedStatus>("status");
    if (status.connected) {
      try { setLink(await hosted<HostedList>("list")); }
      catch (reason) { setLink({ ...status, connected: false, approvals: [] }); setError(String(reason)); }
    } else setLink({ ...status, approvals: [] });
    setShare(await sharing<SharingStatus>("status").catch(() => null));
    const list = await goalRequest<GoalList>("list");
    const waiting = list.goals.filter(goal => goal.state === "awaiting_approval");
    const full = await Promise.all(waiting.map(goal => goalRequest<Goal>("get", { id: goal.id }).catch(() => null)));
    setTasks(full.filter((goal): goal is Goal => !!goal && !!goal.approvals?.length));
  };
  useEffect(() => {
    let disposed = false, busy = false;
    const poll = async () => { if (disposed || busy || (typeof document !== "undefined" && document.visibilityState === "hidden")) return; busy = true; try { await refresh(); } catch (reason) { if (!disposed) setError(String(reason)); } finally { busy = false; } };
    void poll();
    const timer = setInterval(() => void poll(), 4000);
    return () => { disposed = true; clearInterval(timer); };
  }, []);
  useEffect(() => {
    onCount?.((link?.approvals.length ?? 0) + tasks.reduce((n, goal) => n + (goal.approvals?.length ?? 0), 0) + chatWaiting);
  }, [link, tasks, chatWaiting]);

  const run = async (work: () => Promise<void>) => {
    if (pending) return;
    setPending(true); setError(""); setNotice("");
    try { await work(); await refresh(); } catch (reason) { setError(String(reason)); } finally { setPending(false); }
  };
  const answer = (id: string, choice: "approve" | "site" | "reject") => run(async () => {
    const result = await hosted<{ outcome: Outcome }>("decide", { id, choice });
    setNotice(outcomeText(result.outcome, choice));
  });
  const total = (link?.approvals.length ?? 0) + tasks.length + chatWaiting;

  return <section className="approvals-inbox" aria-label="Approvals">
    <header className="task-board-head"><div><h2>Approvals</h2><p>Everything that waits for you, in one place. Send, pay, submit, delete and sign-in steps always stop here or in chat until you answer.</p></div></header>
    {error && <p className="native-error" role="alert">{error}</p>}
    {notice && <p className="native-notice" role="status">{notice}</p>}
    {total === 0 && link && <p className="inbox-empty">Nothing is waiting for you.</p>}

    <h3>From Smara on Telegram <small>{link?.connected ? `Connected · ${link.shop}` : "Not connected"}</small></h3>
    {link && !link.connected && <p className="inbox-empty">Not signed in. Open <b>Hosted</b> in the top bar and press <b>Sign in</b> to answer Smara's steps here as well as in chat.</p>}
    {link && !link.connected && <details className="hosted-details"><summary>Have a code instead?</summary><form className="settings-form inbox-connect" onSubmit={event => { event.preventDefault(); void run(async () => { await hosted("pair", { code }); setCode(""); setNotice("Connected. Steps Smara wants to take on this PC will show here too; the first answer counts."); }); }}>
      <p>Answer the steps Smara wants to take on this PC (browser clicks, files in the Smara Shared folder) here as well as in chat. Send <b>desktop approvals</b> to Smara in Telegram, then paste the code.</p>
      {!link.server && <small>First pair this PC's browser with Smara (Browser panel), so Desktop knows where your Smara is.</small>}
      <label>Code<input value={code} onChange={event => setCode(event.target.value)} placeholder="ABCD-EFGH" autoComplete="off" /></label>
      <button className="primary" disabled={pending || !link.server || !codeLooksRight(code)}>Connect</button>
    </form></details>}
    {link?.approvals.map(item => <article key={item.id} className="inbox-card">
      <div className="task-card-head"><span className="task-agent">{toolLabel(item.tool)}</span><span>{askedLabel(item.asked_at, Date.now())}</span></div>
      <p className="inbox-text">{item.card}</p>
      <div className="task-actions">
        <button className="primary" disabled={pending} onClick={() => void answer(item.id, "approve")}>{item.tool === "hand_off_to_owner" ? "Done, continue" : "Approve"}</button>
        {item.site_choice && <button disabled={pending} onClick={() => void answer(item.id, "site")}>Approve + this site (15 min)</button>}
        <button disabled={pending} onClick={() => void answer(item.id, "reject")}>{item.tool === "hand_off_to_owner" ? "Cancel" : "Reject"}</button>
      </div>
    </article>)}
    {link?.connected && <button className="link" disabled={pending} onClick={() => { if (window.confirm("Stop answering Smara's approvals on this PC? To switch it off on Smara's side too, send 'stop desktop approvals' in chat.")) void run(async () => { await hosted("disconnect"); }); }}>Disconnect approvals on this PC</button>}

    <h3>Share memory with Smara <small>optional</small></h3>
    <div className="inbox-card">
      <p className="inbox-text">{sharingText(share)}</p>
      {share?.connected && <div className="task-actions">
        <label><input type="checkbox" checked={!!share.enabled} disabled={pending} onChange={event => void run(async () => { setShare(await sharing<SharingStatus>("set", { enabled: event.target.checked })); setNotice(event.target.checked ? "Memory sharing is on for this shop." : "Memory sharing is off."); })} /> Share this PC's chat notes with {share.shop}</label>
        <button disabled={pending} onClick={() => { if (window.confirm("Erase everything this PC shared with Smara? This cannot be undone.")) void run(async () => { await sharing("forget"); setNotice("Everything this PC shared was erased from Smara."); }); }}>Forget what I shared</button>
      </div>}
    </div>

    <h3>Background tasks <small>{tasks.length ? `${tasks.length} waiting` : "none waiting"}</small></h3>
    {tasks.map(goal => goal.approvals!.map(request => <article key={goal.id + request.id} className="inbox-card">
      <div className="task-card-head"><span className="task-agent">{goal.spec.objective.slice(0, 70)}</span></div>
      <p className="inbox-text" style={{ whiteSpace: "pre-line" }}>{uploadWarning(request) || commandWarning(request) || approvalText(request)}</p>
      {commandWarning(request) && <details><summary>Show the command</summary><pre>{String(request.params.command ?? "")}</pre></details>}
      <div className="task-actions">
        <button className="primary" disabled={pending || !supportsApproval(request.method, request.params)} onClick={() => void run(async () => { await goalRequest("respond", { id: goal.id, request_id: request.id, allow: true }); setNotice("Approved. The task carries on."); })}>Approve</button>
        <button disabled={pending || !supportsApproval(request.method, request.params)} onClick={() => void run(async () => { await goalRequest("respond", { id: goal.id, request_id: request.id, allow: false }); setNotice("Denied."); })}>Deny</button>
      </div>
    </article>))}

    <h3>This chat <small>{chatWaiting ? `${chatWaiting} waiting` : "none waiting"}</small></h3>
    {chatWaiting > 0 && <p>The open chat is waiting for your answer. <button className="primary" onClick={onShowChat}>Show the chat</button></p>}
  </section>;
}
