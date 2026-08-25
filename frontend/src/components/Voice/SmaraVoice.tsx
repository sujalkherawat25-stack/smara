import { useEffect, useRef, useState } from "react";
import { AudioLines, Mic, PhoneOff, X } from "lucide-react";
import { useChatStore } from "@/stores/chatStore";

type VoiceStatus = "idle" | "connecting" | "listening" | "hearing" | "thinking" | "speaking" | "error" | "limit";

const STATUS_COPY: Record<VoiceStatus, string> = {
  idle: "Start a voice conversation",
  connecting: "Bringing Smara in…",
  listening: "I’m listening",
  hearing: "I hear you…",
  thinking: "Thinking…",
  speaking: "Smara is speaking",
  error: "Voice connection lost",
  limit: "Daily voice limit reached",
};

const LANGUAGES = [
  // Pin Hinglish to Saaras' Hindi acoustic/language model. `unknown`
  // repeatedly misclassified ordinary Hindi as Kannada or Gujarati.
  { value: "auto-hi", stt: "hi-IN", tts: "hi-IN", label: "Auto · Hinglish" },
  { value: "en-IN", stt: "en-IN", tts: "en-IN", label: "English · India" },
  { value: "hi-IN", stt: "hi-IN", tts: "hi-IN", label: "हिन्दी" },
  { value: "bn-IN", stt: "bn-IN", tts: "bn-IN", label: "বাংলা" },
  { value: "ta-IN", stt: "ta-IN", tts: "ta-IN", label: "தமிழ்" },
  { value: "te-IN", stt: "te-IN", tts: "te-IN", label: "తెలుగు" },
  { value: "mr-IN", stt: "mr-IN", tts: "mr-IN", label: "मराठी" },
  { value: "gu-IN", stt: "gu-IN", tts: "gu-IN", label: "ગુજરાતી" },
  { value: "kn-IN", stt: "kn-IN", tts: "kn-IN", label: "ಕನ್ನಡ" },
  { value: "ml-IN", stt: "ml-IN", tts: "ml-IN", label: "മലയാളം" },
  { value: "pa-IN", stt: "pa-IN", tts: "pa-IN", label: "ਪੰਜਾਬੀ" },
];

function socketUrl(language: string, ttsLanguage: string): string {
  const scheme = window.location.protocol === "https:" ? "wss:" : "ws:";
  const params = new URLSearchParams({ language_code: language, tts_language_code: ttsLanguage });
  return `${scheme}//${window.location.host}/v1/memento/voice/ws?${params}`;
}

function downsampleTo16k(input: Float32Array, inputRate: number): Int16Array {
  const ratio = inputRate / 16000;
  const length = Math.max(1, Math.floor(input.length / ratio));
  const output = new Int16Array(length);
  for (let i = 0; i < length; i++) {
    const start = Math.floor(i * ratio);
    const end = Math.min(input.length, Math.floor((i + 1) * ratio));
    let sum = 0;
    for (let j = start; j < end; j++) sum += input[j];
    const sample = Math.max(-1, Math.min(1, sum / Math.max(1, end - start)));
    output[i] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
  }
  return output;
}

function speechText(text: string): string {
  return text
    .replace(/```[\s\S]*?```/g, " code block ")
    .replace(/https?:\/\/\S+/g, "")
    .replace(/[*_#>`~[\]()]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function takeSpeakableChunks(buffer: string, final = false): [string[], string] {
  const chunks: string[] = [];
  let rest = buffer;
  // Full sentences sound best, but an early clause is a much better latency
  // tradeoff than waiting through a long punctuation-light LLM sentence.
  const sentence = /^((?:[\s\S]{14,}?[.!?।]\s+)|(?:[\s\S]{30,}?[,;:]\s+))/;
  while (true) {
    const match = rest.match(sentence);
    if (!match) break;
    chunks.push(speechText(match[1]));
    rest = rest.slice(match[1].length);
  }
  if (rest.length >= 58) {
    const cut = rest.lastIndexOf(" ", 52);
    if (cut > 24) {
      chunks.push(speechText(rest.slice(0, cut)));
      rest = rest.slice(cut + 1);
    }
  }
  if (final && rest.trim()) {
    chunks.push(speechText(rest));
    rest = "";
  }
  return [chunks.filter(Boolean), rest];
}

export default function SmaraVoice() {
  const isStreaming = useChatStore((s) => s.isStreaming);
  const streamingText = useChatStore((s) => s.streamingText);
  const send = useChatStore((s) => s.send);

  const [open, setOpen] = useState(false);
  const [status, setStatus] = useState<VoiceStatus>("idle");
  const [liveText, setLiveText] = useState("");
  const [error, setError] = useState("");
  const [language, setLanguage] = useState("auto-hi");

  const wsRef = useRef<WebSocket | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const captureContextRef = useRef<AudioContext | null>(null);
  const processorRef = useRef<ScriptProcessorNode | null>(null);
  const playbackContextRef = useRef<AudioContext | null>(null);
  const sourcesRef = useRef<Set<AudioBufferSourceNode>>(new Set());
  const nextPlayAtRef = useRef(0);
  const replyCursorRef = useRef(0);
  const replyBufferRef = useRef("");
  const voiceTurnRef = useRef(false);
  const turnLockedRef = useRef(false);
  const ttsCompleteRef = useRef(false);
  const turnMessageStartRef = useRef(0);
  const localSpeechRef = useRef(false);
  const hotFramesRef = useRef(0);
  const quietFramesRef = useRef(0);
  const preRollRef = useRef<ArrayBuffer[]>([]);
  const mountedRef = useRef(true);

  const sendControl = (payload: object) => {
    const ws = wsRef.current;
    if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify(payload));
  };

  const stopPlayback = () => {
    for (const source of sourcesRef.current) {
      try { source.stop(); } catch { /* already stopped */ }
    }
    sourcesRef.current.clear();
    nextPlayAtRef.current = 0;
  };

  const resetLocalVad = () => {
    localSpeechRef.current = false;
    hotFramesRef.current = 0;
    quietFramesRef.current = 0;
    preRollRef.current = [];
  };

  const finishTurn = () => {
    if (!turnLockedRef.current) return;
    if (!ttsCompleteRef.current || sourcesRef.current.size > 0) return;
    turnLockedRef.current = false;
    voiceTurnRef.current = false;
    resetLocalVad();
    sendControl({ type: "turn_complete" });
    setStatus("listening");
  };

  const playPcm24k = async (data: ArrayBuffer) => {
    const ctx = playbackContextRef.current;
    if (!ctx || data.byteLength < 2) return;
    if (ctx.state === "suspended") await ctx.resume();
    const evenBytes = data.byteLength - (data.byteLength % 2);
    const samples = new Int16Array(data.slice(0, evenBytes));
    const audio = ctx.createBuffer(1, samples.length, 24000);
    const channel = audio.getChannelData(0);
    for (let i = 0; i < samples.length; i++) channel[i] = samples[i] / 32768;
    const source = ctx.createBufferSource();
    source.buffer = audio;
    source.connect(ctx.destination);
    const startAt = Math.max(ctx.currentTime + 0.025, nextPlayAtRef.current);
    source.start(startAt);
    nextPlayAtRef.current = startAt + audio.duration;
    sourcesRef.current.add(source);
    source.onended = () => {
      sourcesRef.current.delete(source);
      finishTurn();
    };
  };

  const closeVoice = async () => {
    mountedRef.current = false;
    stopPlayback();
    processorRef.current?.disconnect();
    processorRef.current = null;
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    await captureContextRef.current?.close().catch(() => undefined);
    await playbackContextRef.current?.close().catch(() => undefined);
    captureContextRef.current = null;
    playbackContextRef.current = null;
    wsRef.current?.close(1000, "voice UI closed");
    wsRef.current = null;
    replyCursorRef.current = 0;
    replyBufferRef.current = "";
    voiceTurnRef.current = false;
    turnLockedRef.current = false;
    ttsCompleteRef.current = false;
    turnMessageStartRef.current = 0;
    resetLocalVad();
    setStatus("idle");
    setOpen(false);
  };

  const startVoice = async () => {
    setOpen(true);
    setStatus("connecting");
    setError("");
    setLiveText("");
    mountedRef.current = true;
    try {
      const selected = LANGUAGES.find((item) => item.value === language) ?? LANGUAGES[0];
      const mic = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
          channelCount: 1,
        },
      });
      streamRef.current = mic;
      const capture = new AudioContext();
      const playback = new AudioContext({ sampleRate: 24000 });
      captureContextRef.current = capture;
      playbackContextRef.current = playback;

      const ws = new WebSocket(socketUrl(selected.stt, selected.tts));
      ws.binaryType = "arraybuffer";
      wsRef.current = ws;

      ws.onopen = () => {
        const source = capture.createMediaStreamSource(mic);
        // 1024 samples at 48 kHz ~= 21 ms: responsive without tiny frames.
        const processor = capture.createScriptProcessor(1024, 1, 1);
        processorRef.current = processor;
        processor.onaudioprocess = (event) => {
          const socket = wsRef.current;
          if (socket?.readyState !== WebSocket.OPEN) return;
          event.outputBuffer.getChannelData(0).fill(0);
          // Strict turn-taking: no microphone bytes leave the browser from
          // accepted transcript until Smara's final audio buffer has played.
          if (turnLockedRef.current) return;
          const input = event.inputBuffer.getChannelData(0);
          let energy = 0;
          for (let i = 0; i < input.length; i++) energy += input[i] * input[i];
          const rms = Math.sqrt(energy / Math.max(1, input.length));
          const pcm = downsampleTo16k(input, capture.sampleRate);
          const frame = pcm.buffer.slice(0);
          const threshold = sourcesRef.current.size > 0 ? 0.04 : 0.012;

          if (!localSpeechRef.current) {
            const preRoll = preRollRef.current;
            preRoll.push(frame);
            if (preRoll.length > 8) preRoll.shift();
            hotFramesRef.current = rms >= threshold ? hotFramesRef.current + 1 : 0;
            if (hotFramesRef.current >= 2) {
              localSpeechRef.current = true;
              quietFramesRef.current = 0;
              for (const buffered of preRoll) socket.send(buffered);
              preRollRef.current = [];
            }
          } else {
            socket.send(frame);
            quietFramesRef.current = rms < threshold * 0.7
              ? quietFramesRef.current + 1
              : 0;
            // Keep ~750 ms of trailing silence: enough for normal Sarvam VAD
            // while tolerating a brief thinking pause inside one command.
            if (quietFramesRef.current >= 36) {
              localSpeechRef.current = false;
              hotFramesRef.current = 0;
              quietFramesRef.current = 0;
            }
          }
        };
        source.connect(processor);
        processor.connect(capture.destination);
      };

      ws.onmessage = async (event) => {
        if (event.data instanceof ArrayBuffer) {
          setStatus("speaking");
          await playPcm24k(event.data);
          return;
        }
        const message = JSON.parse(String(event.data));
        switch (message.type) {
          case "ready":
            setStatus("listening");
            break;
          case "speech_start":
            if (turnLockedRef.current) break;
            setLiveText("");
            setStatus("hearing");
            break;
          case "speech_end":
            if (!turnLockedRef.current) setStatus("thinking");
            break;
          case "transcript": {
            const text = String(message.text || "").trim();
            if (!text || turnLockedRef.current) break;
            turnLockedRef.current = true;
            ttsCompleteRef.current = false;
            turnMessageStartRef.current = useChatStore.getState().messages.length;
            resetLocalVad();
            setLiveText(text);
            voiceTurnRef.current = true;
            replyCursorRef.current = 0;
            replyBufferRef.current = "";
            void send(text);
            setStatus("thinking");
            break;
          }
          case "tts_end":
            ttsCompleteRef.current = true;
            finishTurn();
            break;
          case "error":
            setError(message.message || "Voice service unavailable.");
            setStatus("error");
            break;
          case "tts_warning":
            setLiveText(message.message || "I couldn't speak that reply. Please try again.");
            stopPlayback();
            ttsCompleteRef.current = true;
            finishTurn();
            break;
          case "tts_interrupted":
            stopPlayback();
            break;
          case "session_end":
            setError(message.message || "Voice session paused. Reconnect to continue.");
            setStatus("error");
            break;
          case "quota_exceeded":
            stopPlayback();
            turnLockedRef.current = false;
            ttsCompleteRef.current = false;
            setError(message.message || "You’ve used all 10 free voice turns for today.");
            setStatus("limit");
            wsRef.current?.close(1000, "Daily voice limit reached");
            break;
        }
      };
      ws.onerror = () => {
        if (!mountedRef.current) return;
        setError("Couldn’t reach Smara’s voice service. Check Sarvam configuration and reconnect.");
        setStatus("error");
      };
      ws.onclose = (event) => {
        if (!mountedRef.current || event.code === 1000) return;
        setError(event.reason || "Voice connection closed. Tap reconnect to try again.");
        setStatus("error");
      };
    } catch {
      setError("Microphone unavailable. Allow microphone access, then try again.");
      setStatus("error");
    }
  };

  // Feed new LLM text into TTS before the full reply finishes.
  useEffect(() => {
    if (!voiceTurnRef.current || wsRef.current?.readyState !== WebSocket.OPEN) return;
    // chatStore clears streamingText in the same state update that marks a
    // completed turn. Preserve our unsent tail; the completion effect below
    // flushes it. A shrink while still streaming is the real stream_reset.
    if (!isStreaming && streamingText.length === 0) return;
    if (streamingText.length < replyCursorRef.current) {
      stopPlayback();
      sendControl({ type: "interrupt" });
      replyCursorRef.current = 0;
      replyBufferRef.current = "";
    }
    const delta = streamingText.slice(replyCursorRef.current);
    replyCursorRef.current = streamingText.length;
    replyBufferRef.current += delta;
    const [chunks, rest] = takeSpeakableChunks(replyBufferRef.current);
    replyBufferRef.current = rest;
    if (chunks.length > 0) ttsCompleteRef.current = false;
    for (const chunk of chunks) sendControl({ type: "speak", text: chunk });
  }, [streamingText, isStreaming]);

  // Complete one locked turn, then flush its final text into TTS.
  useEffect(() => {
    if (!isStreaming && voiceTurnRef.current) {
      // React can batch every token update together with the final `done`
      // update for very short/fast answers. In that case this component
      // never renders an intermediate streamingText value even though the
      // completed assistant message is safely stored. Reconstruct any
      // unseen suffix from that durable final message before flushing TTS.
      const messages = useChatStore.getState().messages;
      let finalAnswer = "";
      for (let i = messages.length - 1; i >= turnMessageStartRef.current; i--) {
        if (messages[i].role === "assistant") {
          finalAnswer = messages[i].content;
          break;
        }
      }
      if (finalAnswer.length > replyCursorRef.current) {
        replyBufferRef.current += finalAnswer.slice(replyCursorRef.current);
        replyCursorRef.current = finalAnswer.length;
      }
      const [chunks] = takeSpeakableChunks(replyBufferRef.current, true);
      if (chunks.length > 0) {
        ttsCompleteRef.current = false;
        setStatus("speaking");
        for (const chunk of chunks) sendControl({ type: "speak", text: chunk });
        sendControl({ type: "flush_tts" });
      } else {
        // No assistant message (network failure) or nothing speakable.
        // Release both browser and server locks instead of deadlocking.
        ttsCompleteRef.current = true;
        finishTurn();
      }
      replyBufferRef.current = "";
      replyCursorRef.current = 0;
      voiceTurnRef.current = false;
    }
  }, [isStreaming, send]);

  useEffect(() => () => { void closeVoice(); }, []);

  if (!open) {
    return (
      <button
        onClick={() => void startVoice()}
        className="shrink-0 grid place-items-center w-8 h-8 rounded-xl transition-colors duration-150"
        style={{ color: "var(--accent)" }}
        title="Talk with Smara live"
        onMouseEnter={(e) => { e.currentTarget.style.background = "var(--bg-elevated)"; }}
        onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}
      >
        <AudioLines size={16} />
      </button>
    );
  }

  return (
    <div className="fixed inset-0 z-50 grid place-items-center px-5" style={{ background: "rgba(10,9,8,0.72)", backdropFilter: "blur(14px)" }}>
      <div className="relative w-full max-w-md overflow-hidden rounded-[32px] border px-7 py-8 text-center" style={{ background: "var(--bg-base)", borderColor: "var(--border-default)", boxShadow: "0 28px 90px rgba(0,0,0,.45)" }}>
        <button onClick={() => void closeVoice()} className="absolute right-5 top-5 grid h-9 w-9 place-items-center rounded-full" style={{ color: "var(--text-muted)", background: "var(--bg-elevated)" }} aria-label="Close voice conversation">
          <X size={16} />
        </button>

        <div className="mx-auto mt-5 grid h-32 w-32 place-items-center rounded-full" style={{
          background: status === "hearing"
            ? "radial-gradient(circle, rgba(201,169,110,.38), rgba(201,169,110,.08) 58%, transparent 70%)"
            : "radial-gradient(circle, rgba(139,112,214,.35), rgba(139,112,214,.07) 58%, transparent 70%)",
          animation: ["hearing", "speaking", "connecting"].includes(status) ? "pulse 1.35s ease-in-out infinite" : undefined,
        }}>
          <div className="grid h-20 w-20 place-items-center rounded-full" style={{ background: "linear-gradient(145deg, var(--accent), var(--accent2))", color: "white", boxShadow: "0 10px 38px rgba(160,125,210,.35)" }}>
            {status === "hearing" ? <Mic size={30} /> : <AudioLines size={32} />}
          </div>
        </div>

        <h2 className="mt-6 text-2xl font-semibold" style={{ color: "var(--text-primary)" }}>Smara</h2>
        <p className="mt-1 text-sm" style={{ color: ["error", "limit"].includes(status) ? "#ef4444" : "var(--text-secondary)" }}>{STATUS_COPY[status]}</p>

        <div className="mt-6 min-h-16 rounded-2xl px-4 py-3 text-sm leading-6" style={{ background: "var(--bg-elevated)", color: "var(--text-secondary)" }}>
          {error || liveText || "Speak one request. I’ll listen again after Smara finishes answering."}
        </div>

        <div className="mt-5 flex items-center justify-center gap-3">
          <select value={language} onChange={(e) => setLanguage(e.target.value)} disabled={status !== "error"} className="rounded-xl border px-3 py-2 text-xs outline-none" style={{ background: "var(--bg-elevated)", borderColor: "var(--border-default)", color: "var(--text-secondary)", opacity: status === "error" ? 1 : 0.7 }} title="Change language after disconnecting">
            {LANGUAGES.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
          </select>
          {status === "error" ? (
            <button onClick={() => { void closeVoice().then(startVoice); }} className="rounded-xl px-4 py-2 text-xs font-medium text-white" style={{ background: "var(--accent)" }}>Reconnect</button>
          ) : (
            <button onClick={() => void closeVoice()} className="grid h-11 w-11 place-items-center rounded-full bg-red-500 text-white" title="End voice conversation">
              <PhoneOff size={18} />
            </button>
          )}
        </div>

        <p className="mt-5 text-[11px]" style={{ color: "var(--text-dim)" }}>Saaras v3 · Shreya on Bulbul v3 · strict turn-taking</p>
      </div>
    </div>
  );
}
