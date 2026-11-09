import { test } from "node:test";
import assert from "node:assert/strict";
import { freshState, makeKeyStore, parseState, serializeState } from "../src/state.js";

test("auth state survives a JSON round trip with its bytes intact", () => {
  const state = freshState();
  state.keys["pre-key"] = { 7: { public: Buffer.from([1, 2, 3]), private: Buffer.from("secret-bytes") } };
  const back = parseState(serializeState(state));
  assert.equal(back.creds.registrationId, state.creds.registrationId);
  assert.deepEqual(Buffer.from(back.keys["pre-key"][7].private).toString(), "secret-bytes");
});

test("a state without credentials is refused", () => {
  assert.throws(() => parseState(JSON.stringify({ keys: {} })), /invalid auth state/);
  assert.throws(() => parseState("not json"));
});

test("the key store keeps values in memory, reports changes, and deletes on null", async () => {
  const keys = {};
  let changes = 0;
  const store = makeKeyStore(keys, () => changes++);
  await store.set({ session: { "919@s": { blob: Buffer.from("x") } } });
  assert.equal(changes, 1);
  assert.ok(keys.session["919@s"]);
  const got = await store.get("session", ["919@s", "missing"]);
  assert.ok(got["919@s"]);
  assert.equal("missing" in got, false);
  await store.set({ session: { "919@s": null } });
  assert.equal(keys.session["919@s"], undefined);
});
