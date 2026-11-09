// Baileys auth state (device credentials + Signal keys) held in memory only.
//
// The sidecar never writes auth files. Every change is pushed to the Smara app, which stores it encrypted in the
// database through the vault (tenant_secrets, name "wa_link_auth"). On restart the app hands the stored state back.
import { BufferJSON, initAuthCreds, proto } from "@whiskeysockets/baileys";

export function freshState() {
  return { creds: initAuthCreds(), keys: {} };
}

// One JSON string for the whole state; Buffers survive the round trip through BufferJSON.
export function serializeState(state) {
  return JSON.stringify({ creds: state.creds, keys: state.keys }, BufferJSON.replacer);
}

export function parseState(text) {
  const raw = JSON.parse(text, BufferJSON.reviver);
  if (!raw || typeof raw !== "object" || !raw.creds || typeof raw.creds !== "object") {
    throw new Error("invalid auth state");
  }
  return { creds: raw.creds, keys: raw.keys && typeof raw.keys === "object" ? raw.keys : {} };
}

// The `keys` store Baileys expects (get/set), backed by the in-memory map. `onChange` runs after every write.
export function makeKeyStore(keys, onChange = () => {}) {
  return {
    keys,
    async get(type, ids) {
      const bucket = keys[type] || {};
      const out = {};
      for (const id of ids) {
        let value = bucket[id];
        if (value) {
          if (type === "app-state-sync-key") value = proto.Message.AppStateSyncKeyData.fromObject(value);
          out[id] = value;
        }
      }
      return out;
    },
    async set(data) {
      for (const type of Object.keys(data)) {
        keys[type] ||= {};
        for (const id of Object.keys(data[type])) {
          const value = data[type][id];
          if (value) keys[type][id] = value;
          else delete keys[type][id];
        }
      }
      onChange();
    },
  };
}
