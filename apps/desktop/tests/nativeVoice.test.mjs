import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";

const source = readFileSync(new URL("../src/NativeVoice.tsx", import.meta.url), "utf8");
const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true } }).outputText;
function fixture({ readAloud = false, failRecorder = false, playbackError = false } = {}) {
  const slots = [], effects = [], calls = []; let cursor = 0, dirty = true, tree, cleanup;
  let stoppedTracks = 0;
  const hooks = {
    useState(initial) { const index = cursor++; if (!(index in slots)) slots[index] = initial; return [slots[index], value => { slots[index] = value; dirty = true; }]; },
    useRef(initial) { const index = cursor++; return slots[index] ??= { current: initial }; },
    useEffect(effect) { const index = cursor++; if (!slots[index]) { slots[index] = true; effects.push(effect); } },
  };
  const jsx = (type, props) => ({ type, props: props || {} });
  const module = { exports: {} }; let instance;
  class Recorder {
    static isTypeSupported() { return true; }
    state = "inactive";
    constructor() { if (failRecorder) throw new Error("Synthetic recorder failure"); instance = this; }
    start() { this.state = "recording"; }
    stop() { this.state = "inactive"; this.ondataavailable({ data: new Blob(["synthetic audio"]) }); this.onstop(); }
  }
  class Audio {
    play() { return playbackError ? Promise.reject(new Error("Synthetic playback failure")) : Promise.resolve(); }
    pause() {}
  }
  const require = name => name === "react" ? hooks : name === "react/jsx-runtime" ? { jsx, jsxs: jsx } : { invoke: async (_name, { request }) => { calls.push(request); return { transcript: "Synthetic voice transcript", audio: btoa("synthetic audio") }; } };
  vm.runInNewContext(code, { module, exports: module.exports, require, navigator: { mediaDevices: { getUserMedia: async () => ({ getTracks: () => [{ stop() { stoppedTracks++; } }] }) } }, MediaRecorder: Recorder, Audio, URL: { createObjectURL: () => "blob:synthetic", revokeObjectURL() {} }, Blob, Uint8Array, btoa, atob, window: { confirm: () => readAloud }, setTimeout: () => 1, clearTimeout() {} });
  let transcript = "";
  const flush = async () => { for (let i = 0; i < 25; i++) { if (dirty) { dirty = false; cursor = 0; tree = readAloud ? module.exports.ReadAloud({ text: "Synthetic sentence. ".repeat(200) }) : module.exports.default({ disabled: false, onTranscript: text => { transcript = text; } }); while (effects.length) cleanup = effects.shift()(); } await Promise.resolve(); } };
  const nodes = () => { const result = []; function walk(n) { if (Array.isArray(n)) n.forEach(walk); else if (n && typeof n === "object") { result.push(n); walk(n.props.children); } } walk(tree); return result; };
  const button = label => nodes().find(n => n.type === "button" && n.props.children === label);
  return { module, calls, flush, button, nodes, close: () => cleanup(), transcript: () => transcript, stop: () => instance.stop(), stoppedTracks: () => stoppedTracks };
}

test("long read-aloud text is chunked without splitting surrogate pairs", () => {
  const f = fixture(); const text = "😀".repeat(4000);
  const chunks = f.module.exports.speechChunks(text);
  assert.equal(chunks.join(""), text); assert(chunks.every(c => c.length <= 500));
});
test("packaged CSP permits local speech blobs but not remote media", () => {
  const config = JSON.parse(readFileSync(new URL("../src-tauri/tauri.conf.json", import.meta.url), "utf8"));
  const directive = config.app.security.csp.split(";").map(v => v.trim()).find(v => v.startsWith("media-src "));
  assert.equal(directive, "media-src 'self' blob:");
});
test("recording does not upload until explicit transcription and returns a draft only", async () => {
  const f = fixture(); await f.flush(); await f.button("Voice").props.onClick(); await f.flush(); f.stop(); await f.flush();
  assert.equal(f.calls.length, 0); assert.equal(f.transcript(), "");
  await f.button("Transcribe with Sarvam").props.onClick(); await f.flush();
  assert.equal(f.calls.length, 1); assert.equal(f.calls[0].operation, "voice"); assert.equal(f.transcript(), "Synthetic voice transcript");
});
test("discarding a local recording uploads nothing", async () => {
  const f = fixture(); await f.flush(); await f.button("Voice").props.onClick(); await f.flush(); f.stop(); await f.flush();
  f.button("Discard").props.onClick(); await f.flush(); assert.equal(f.calls.length, 0);
  assert.equal(f.button("Transcribe with Sarvam"), undefined);
});
test("unmount stops recording without publishing a clip or making a request", async () => {
  const f = fixture(); await f.flush(); await f.button("Voice").props.onClick(); await f.flush(); f.close(); await f.flush();
  assert.equal(f.calls.length, 0); assert.equal(f.transcript(), "");
});
test("recording setup failures release the microphone without uploading", async () => {
  const f = fixture({ failRecorder: true }); await f.flush(); f.button("Voice").props.onClick(); await f.flush();
  assert.equal(f.stoppedTracks(), 1); assert.equal(f.calls.length, 0);
  assert(f.nodes().some(n => n.props.role === "alert" && String(n.props.children).includes("Synthetic recorder failure")));
});
test("playback failures remain visible and stop further speech requests", async () => {
  const f = fixture({ readAloud: true, playbackError: true }); await f.flush(); f.button("Read aloud").props.onClick(); await f.flush();
  assert.equal(f.calls.length, 1); assert(f.button("Read aloud"));
  assert(f.nodes().some(n => n.props.role === "alert" && String(n.props.children).includes("Synthetic playback failure")));
});
