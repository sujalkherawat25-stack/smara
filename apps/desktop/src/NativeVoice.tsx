import { useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";

export const voiceRequest = <T,>(action: string, values: Record<string, unknown> = {}) =>
  invoke<T>("native_manage", { request: { operation: "voice", voice: { action, ...values } } });

export function speechChunks(text: string): string[] {
  // Keep returned WAV clips comfortably below the adapter's audio/IPC caps;
  // Sarvam's maximum input length is not a safe target for a whole audio clip.
  const limit = 500;
  const chunks: string[] = [];
  let rest = text.trim();
  while (rest) {
    let end = Math.min(limit, rest.length);
    if (end < rest.length) {
      const boundary = Math.max(rest.lastIndexOf("\n", end), rest.lastIndexOf(". ", end), rest.lastIndexOf(" ", end));
      if (boundary > limit / 2) end = boundary;
      if (/^[\uDC00-\uDFFF]$/.test(rest[end])) end--;
    }
    chunks.push(rest.slice(0, end).trim()); rest = rest.slice(end).trim();
  }
  return chunks;
}

export function ReadAloud({ text, disabled = false }: { text: string; disabled?: boolean }) {
  const [playing, setPlaying] = useState(false), [error, setError] = useState("");
  const generation = useRef(0), player = useRef<HTMLAudioElement | null>(null);
  const release = useRef<(() => void) | null>(null);
  const stop = () => { generation.current++; player.current?.pause(); release.current?.(); release.current = null; player.current = null; setPlaying(false); };
  useEffect(() => { setPlaying(false); return () => { generation.current++; player.current?.pause(); release.current?.(); }; }, [text]);
  const speak = async () => {
    if (playing) { stop(); return; }
    const chunks = speechChunks(text);
    if (!chunks.length || !window.confirm(`Read aloud sends this response to Sarvam in ${chunks.length} speech request(s). Speech is billed separately. Continue?`)) return;
    const run = ++generation.current; setPlaying(true); setError("");
    try {
      for (const chunk of chunks) {
        if (generation.current !== run) break;
        const response = await voiceRequest<{ audio: string }>("synthesize", { text: chunk, language: "en-IN" });
        if (generation.current !== run) break;
        const raw = Uint8Array.from(atob(response.audio), char => char.charCodeAt(0));
        const url = URL.createObjectURL(new Blob([raw], { type: "audio/wav" }));
        const audio = new Audio(url); player.current = audio;
        await new Promise<void>((resolve, reject) => {
          let settled = false;
          const finish = (reason?: unknown) => {
            if (settled) return;
            settled = true; URL.revokeObjectURL(url); audio.onended = null; audio.onerror = null;
            if (reason) reject(reason); else resolve();
          };
          release.current = () => finish(); audio.onended = () => finish();
          audio.onerror = () => finish(new Error("Audio playback is unavailable"));
          void audio.play().catch(reason => finish(reason));
        });
        release.current = null;
      }
    } catch (reason) { if (generation.current === run) setError(String(reason)); }
    finally { if (generation.current === run) { setPlaying(false); player.current = null; } }
  };
  return <span className="voice-read"><button type="button" className="text-button" disabled={!playing && (disabled || !text.trim())} onClick={() => void speak()}>{playing ? "Stop audio" : "Read aloud"}</button>{error && <small role="alert">{error}</small>}</span>;
}

export default function NativeVoice({ disabled, onTranscript }: { disabled: boolean; onTranscript: (text: string) => void }) {
  const [recording, setRecording] = useState(false), [sending, setSending] = useState(false), [clip, setClip] = useState<Blob | null>(null), [error, setError] = useState("");
  const [language, setLanguage] = useState("unknown");
  const recorder = useRef<MediaRecorder | null>(null), stream = useRef<MediaStream | null>(null), generation = useRef(0), timer = useRef<ReturnType<typeof setTimeout>>();
  const cancel = () => { generation.current++; clearTimeout(timer.current); if (recorder.current?.state === "recording") recorder.current.stop(); stream.current?.getTracks().forEach(track => track.stop()); stream.current = null; setRecording(false); setClip(null); setSending(false); };
  useEffect(() => () => { generation.current++; clearTimeout(timer.current); if (recorder.current?.state === "recording") recorder.current.stop(); stream.current?.getTracks().forEach(track => track.stop()); }, []);
  const record = async () => {
    if (recording) { recorder.current?.stop(); return; }
    const run = ++generation.current; setError(""); setClip(null);
    try {
      if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") throw new Error("Microphone recording is unavailable in this window. Use smara voice transcribe for an audio file.");
      const media = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (generation.current !== run) { media.getTracks().forEach(track => track.stop()); return; }
      stream.current = media;
      const mime = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus"].find(type => MediaRecorder.isTypeSupported(type));
      if (!mime) { media.getTracks().forEach(track => track.stop()); throw new Error("This window has no supported audio recording format"); }
      const instance = new MediaRecorder(media, { mimeType: mime, audioBitsPerSecond: 64000 }); recorder.current = instance;
      const parts: BlobPart[] = []; let size = 0;
      instance.ondataavailable = event => { size += event.data.size; parts.push(event.data); if (size > 2 * 1024 * 1024 && instance.state === "recording") instance.stop(); };
      instance.onerror = () => { if (generation.current === run) { cancel(); setError("Microphone recording failed. Nothing was uploaded."); } };
      instance.onstop = () => { clearTimeout(timer.current); media.getTracks().forEach(track => track.stop()); if (generation.current === run) { setRecording(false); if (size > 2 * 1024 * 1024) setError("Recording is too large. Record a shorter clip."); else setClip(new Blob(parts, { type: mime })); } };
      instance.start(250); setRecording(true); timer.current = setTimeout(() => { if (instance.state === "recording") instance.stop(); }, 25000);
    } catch (reason) {
      if (generation.current === run) {
        clearTimeout(timer.current); stream.current?.getTracks().forEach(track => track.stop());
        stream.current = null; recorder.current = null; setRecording(false); setError(String(reason));
      }
    }
  };
  const transcribe = async () => {
    if (!clip || sending) return;
    const run = generation.current; setSending(true); setError("");
    try {
      const bytes = new Uint8Array(await clip.arrayBuffer()); let binary = "";
      for (let offset = 0; offset < bytes.length; offset += 8192) binary += String.fromCharCode(...bytes.subarray(offset, offset + 8192));
      const result = await voiceRequest<{ transcript: string }>("transcribe", { audio: btoa(binary), mime: clip.type, language });
      if (generation.current === run) { onTranscript(result.transcript); setClip(null); }
    } catch (reason) { if (generation.current === run) setError(String(reason)); }
    finally { if (generation.current === run) setSending(false); }
  };
  return <div className="voice-input"><button type="button" disabled={!recording && (disabled || sending)} onClick={() => void record()} aria-label={recording ? "Stop recording" : "Record voice"} title="Record locally, up to 25 seconds. Nothing is sent until you choose Transcribe.">{recording ? "■ Stop recording" : "Voice"}</button>
    {recording && <small role="status">Recording locally · stops after 25 seconds</small>}
    {clip && <div className="voice-review"><small>Transcription sends this clip to Sarvam. Review the text before sending your message.</small><select aria-label="Speech language" disabled={sending} value={language} onChange={event => setLanguage(event.target.value)}><option value="unknown">Detect language</option><option value="en-IN">English</option><option value="hi-IN">Hindi</option></select><button type="button" disabled={sending || disabled} onClick={() => void transcribe()}>{sending ? "Transcribing…" : "Transcribe with Sarvam"}</button><button type="button" onClick={cancel}>Discard</button></div>}
    {error && <small role="alert">{error}</small>}
  </div>;
}
