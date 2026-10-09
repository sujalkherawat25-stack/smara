// Synthetic transport tests, not a native runtime or live-model acceptance.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";

function fixture({ earlyExit = false, startError = false, timeout = false, stopError = false, initializeError = false } = {}) {
  let listener;
  let unlistened = false;
  const sent = [];
  const events = [];
  const emit = (message, generation = "fixture-generation") => listener({ payload: { generation, message } });
  const invoke = async (command, args) => {
    sent.push({ command, args });
    if (command === "native_stop" && stopError) { stopError = false; throw new Error("Fixture IPC failure"); }
    if (command === "native_start") {
      if (startError) throw new Error("Already connected");
      if (earlyExit) emit({ method: "smara/runtimeStopped" });
      return { generation: "fixture-generation" };
    }
    if (command === "native_send" && args.message.method === "initialize") emit(initializeError
      ? { id: args.message.id, error: { message: "Fixture initialization failure" } }
      : { id: args.message.id, result: { ok: true } });
  };
  const module = { exports: {} };
  const source = readFileSync(new URL("../src/nativeRpc.ts", import.meta.url), "utf8");
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
  vm.runInNewContext(code, {
    module, exports: module.exports, setTimeout: (callback, ms) => setTimeout(callback, timeout ? 5 : ms), clearTimeout,
    require: name => name.endsWith("/core") ? { invoke } : { listen: async (_name, callback) => {
      listener = callback; return () => { unlistened = true; };
    } },
  });
  const client = new module.exports.NativeRpc(message => events.push(message));
  return { client, emit, sent, events, unlistened: () => unlistened };
}

test("native initialization and approval responses stay in the native protocol", async () => {
  const f = fixture();
  await f.client.connect("synthetic-workspace", "synthetic-model");
  assert.equal(f.sent.find(item => item.args?.message?.method === "initialize").args.message.params.clientInfo.name, "smara_desktop");
  f.emit({ id: "permission", method: "item/commandExecution/requestApproval", params: { command: "synthetic" } });
  await f.client.respond("permission", { decision: "decline" });
  assert.equal(f.sent.at(-1).args.message.id, "permission");
  assert.equal(f.sent.at(-1).args.message.result.decision, "decline");
  await f.client.stop();
  assert.equal(f.sent.at(-1).command, "native_stop");
  assert.equal(f.sent.at(-1).args.generation, "fixture-generation");
  assert.equal(f.unlistened(), true);
});

test("stale generation cannot settle a new native request", async () => {
  const f = fixture();
  await f.client.connect("workspace", "model");
  const pending = f.client.request("thread/list");
  const id = f.sent.at(-1).args.message.id;
  f.emit({ id, result: { data: ["stale"] } }, "old-generation");
  f.emit({ id, result: { data: ["current"] } });
  assert.equal((await pending).data[0], "current");
  await f.client.stop();
});

test("runtime exit rejects pending work without manufacturing completion", async () => {
  const f = fixture();
  await f.client.connect("workspace", "model");
  const pending = f.client.request("turn/start");
  f.emit({ method: "smara/runtimeStopped" });
  await assert.rejects(pending, /runtime exited/);
  await assert.rejects(f.client.request("turn/start"), /reconnect/);
  await f.client.stop();
});

test("bootstrap exit before native_start acknowledgment is not lost", async () => {
  const f = fixture({ earlyExit: true });
  await assert.rejects(f.client.connect("workspace", "model"), /runtime exited/);
  assert.equal(f.events[0].method, "smara/runtimeStopped");
  assert.equal(f.unlistened(), true);
});

test("failed connection does not terminate another client's runtime", async () => {
  const f = fixture({ startError: true });
  await assert.rejects(f.client.connect("workspace", "model"), /Already connected/);
  assert.equal(f.sent.some(item => item.command === "native_stop"), false);
  assert.equal(f.unlistened(), true);
});

test("timeout is an unknown outcome and never resends the action", async () => {
  const f = fixture({ timeout: true });
  await f.client.connect("workspace", "model");
  await assert.rejects(f.client.request("turn/start"), error => error.constructor.name === "RpcOutcomeUnknown" && /outcome is unknown/.test(error.message));
  assert.equal(f.sent.filter(item => item.args?.message?.method === "turn/start").length, 1);
  await f.client.stop();
});

test("failed shutdown retains its generation for safe cleanup retry", async () => {
  const f = fixture({ stopError: true });
  await f.client.connect("workspace", "model");
  await assert.rejects(f.client.stop(), /IPC failure/);
  assert.equal(f.client.generation, "fixture-generation");
  await assert.rejects(f.client.request("turn/start"), /reconnect/);
  await f.client.stop();
  assert.equal(f.client.generation, "");
  assert.equal(f.sent.filter(item => item.command === "native_stop").length, 2);
  assert.equal(f.unlistened(), true);
});
test("initialization and cleanup failure remain visible and cleanup can retry", async () => {
  const f = fixture({ initializeError: true, stopError: true });
  await assert.rejects(f.client.connect("workspace", "model"), /initialization failure.*cleanup is unconfirmed.*IPC failure/);
  assert.equal(f.client.generation, "fixture-generation");
  assert.equal(f.unlistened(), false);
  await f.client.stop();
  assert.equal(f.client.generation, "");
  assert.equal(f.unlistened(), true);
});
