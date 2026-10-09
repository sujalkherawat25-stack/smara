import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";

export type RpcMessage = { id?: number | string; method?: string; params?: Record<string, unknown>; result?: unknown; error?: { message: string } };
type Pending = { resolve: (value: unknown) => void; reject: (error: Error) => void; timer: ReturnType<typeof setTimeout> };

export class RpcOutcomeUnknown extends Error {}

/** Transport only: source-built Rust is the session/tool/approval authority. */
export class NativeRpc {
  generation = "";
  private nextId = 0;
  private pending = new Map<number | string, Pending>();
  private unlisten?: UnlistenFn;
  private exited = false;
  constructor(private onMessage: (message: RpcMessage) => void) {}

  async connect(workspace: string, profileId: string, toolsEnabled = false) {
    if (this.generation) throw new Error("Native runtime is already connected");
    this.exited = false;
    // A bootstrap failure can arrive before native_start's response. Preserve
    // those events until the server-assigned generation is known.
    const early: { generation: string; message: RpcMessage }[] = [];
    this.unlisten = await listen<{ generation: string; message: RpcMessage }>("native-runtime-event", event => {
      if (!this.generation) { early.push(event.payload); return; }
      if (event.payload.generation !== this.generation) return;
      this.receive(event.payload.message);
    });
    try {
      const start = await invoke<{ generation: string }>("native_start", { workspace, profileId, toolsEnabled });
      this.generation = start.generation;
      for (const event of early) if (event.generation === this.generation) this.receive(event.message);
      await this.request("initialize", { clientInfo: { name: "smara_desktop", title: "Smara Desktop", version: "0.1.8" }, capabilities: null });
      await this.notify("initialized");
    } catch (error) {
      // Only stop a process this client actually owns; a failed start must
      // not terminate another already-connected client.
      if (this.generation) {
        try { await this.stop(); }
        catch (cleanup) { throw new Error(`${String(error)}. Native cleanup is unconfirmed: ${String(cleanup)}. Reconnect to retry cleanup.`); }
      }
      else { this.unlisten?.(); this.unlisten = undefined; }
      throw error;
    }
  }

  private receive(message: RpcMessage) {
    if (message.id !== undefined && !message.method) {
      const call = this.pending.get(message.id);
      if (!call) return;
      this.pending.delete(message.id);
      clearTimeout(call.timer);
      if (message.error) call.reject(new Error(message.error.message));
      else call.resolve(message.result);
    } else {
      if (message.method === "smara/runtimeStopped") {
        this.exited = true;
        this.rejectPending("Native runtime exited");
      }
      this.onMessage(message);
    }
  }

  request<T = unknown>(method: string, params: Record<string, unknown> = {}): Promise<T> {
    const id = ++this.nextId;
    return new Promise<T>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new RpcOutcomeUnknown(`Native request timed out: ${method}. Its outcome is unknown; do not automatically repeat it.`));
      }, 60000);
      this.pending.set(id, { resolve: value => resolve(value as T), reject, timer });
      void this.send({ id, method, params }).catch(error => {
        clearTimeout(timer); this.pending.delete(id); reject(new RpcOutcomeUnknown(String(error)));
      });
    });
  }

  notify(method: string, params: Record<string, unknown> = {}) { return this.send({ method, params }); }
  respond(id: number | string, result: Record<string, unknown>) { return this.send({ id, result }); }
  private send(message: RpcMessage) {
    if (!this.generation) return Promise.reject(new Error("Connect the native runtime first"));
    if (this.exited) return Promise.reject(new Error("Native runtime exited; reconnect before sending work"));
    return invoke<void>("native_send", { generation: this.generation, message });
  }
  private rejectPending(reason: string) {
    for (const call of this.pending.values()) { clearTimeout(call.timer); call.reject(new Error(reason)); }
    this.pending.clear();
  }
  async stop() {
    let confirmed = false;
    try {
      if (this.generation) await invoke("native_stop", { generation: this.generation });
      confirmed = true;
    }
    finally {
      // Keep the owning generation after an IPC failure so reconnect can retry
      // cleanup, rather than forgetting a possibly running native process.
      if (confirmed) this.generation = "";
      this.exited = true;
      this.rejectPending("Native runtime disconnected");
      if (confirmed) { this.unlisten?.(); this.unlisten = undefined; }
    }
  }
}
